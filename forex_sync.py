#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Forex Factory -> Apple Calendar
Idempotent. Peut être lancé aussi souvent que voulu (login, réveil, dimanche 20h, manuel).

Regles :
  - Devises : EUR, USD, JPY
  - Impacts : High (news rouges) + Holiday (jours feries)
  - Semaine cible : lundi -> vendredi (heure de Paris)
  - High  -> calendrier "Trading News", 15 min, 1 alerte a l'heure exacte
  - Holiday -> calendrier "Events", all-day, alertes veille 20h00 + jour meme 9h00
  - Evenements de meme devise a la meme heure exacte -> fusionnes, 1 seule alerte

Usage :
  forex_sync.py            run normal (fast-path si le feed n'a pas change)
  forex_sync.py --force    ignore le fast-path, reconcilie contre Calendar
  forex_sync.py --dry-run  n'ecrit rien, affiche ce qui serait fait
"""

import datetime
import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from zoneinfo import ZoneInfo

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CURRENCIES = ("EUR", "USD", "JPY")
CAL_NEWS = "Trading News"
CAL_HOLIDAY = "Events"
PARIS = ZoneInfo("Europe/Paris")

# Noms reels resolus au demarrage. Les calendriers Apple tolerent les
# espaces de fin dans leur nom ("Events ") alors qu'AppleScript exige le
# nom exact -> on resout par comparaison souple avant toute operation.
CALS = {"news": CAL_NEWS, "holiday": CAL_HOLIDAY}

# Supprimer les evenements marques ff-key qui ne sont plus dans le feed
# (horaire revise, evenement annule). Ne touche jamais aux evenements
# que le script n'a pas crees lui-meme, ni a ceux deja passes.
REMOVE_STALE = True

STATE_DIR = os.path.expanduser("~/Library/Application Support/ForexSync")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
LOG_FILE = os.path.expanduser("~/Library/Logs/forexsync.log")

# Abreviations : premier motif trouve (en minuscules) gagne.
ABBREV = [
    ("non-farm employment change", "NFP"),
    ("non-farm payrolls", "NFP"),
    ("benchmark payrolls revision", "Payrolls Revision"),
    ("core pce price index", "Core PCE"),
    ("pce price index", "PCE"),
    ("core cpi", "Core CPI"),
    ("cpi flash estimate", "CPI Flash"),
    ("consumer price index", "CPI"),
    ("cpi", "CPI"),
    ("federal funds rate", "FOMC Rate"),
    ("fomc statement", "FOMC"),
    ("fomc press conference", "FOMC Presser"),
    ("fomc economic projections", "FOMC Projections"),
    ("fomc meeting minutes", "FOMC Minutes"),
    ("main refinancing rate", "ECB Rate"),
    ("ecb press conference", "ECB Presser"),
    ("monetary policy statement", "Policy Statement"),
    ("boj policy rate", "BOJ Rate"),
    ("boj press conference", "BOJ Presser"),
    ("boj outlook report", "BOJ Outlook"),
    ("flash manufacturing pmi", "PMI"),
    ("flash services pmi", "Services PMI"),
    ("ism manufacturing pmi", "ISM PMI"),
    ("ism services pmi", "ISM Services"),
    ("manufacturing pmi", "PMI"),
    ("services pmi", "Services PMI"),
    ("advance gdp", "GDP"),
    ("prelim gdp", "GDP"),
    ("final gdp", "GDP Final"),
    ("gdp", "GDP"),
    ("unemployment claims", "Claims"),
    ("unemployment rate", "Unemp Rate"),
    ("average hourly earnings", "Earnings"),
    ("retail sales", "Retail Sales"),
    ("ppi", "PPI"),
    ("consumer confidence", "Consumer Confidence"),
    ("consumer sentiment", "Consumer Sentiment"),
    ("fed chair", "Fed Chair"),
    ("fed chairman", "Fed Chair"),
    ("president lagarde", "Lagarde"),
    ("governor ueda", "Ueda"),
]

# Ordre d'importance pour les fusions (le plus important en premier).
PRIORITY = [
    "NFP", "FOMC Rate", "FOMC", "CPI", "Core CPI", "Core PCE", "ECB Rate",
    "BOJ Rate", "FOMC Presser", "ECB Presser", "BOJ Presser", "Fed Chair",
    "GDP", "PPI", "Retail Sales", "PMI", "ISM PMI", "Claims", "Unemp Rate",
    "Earnings",
]


# --------------------------------------------------------------------------
# Utilitaires
# --------------------------------------------------------------------------

def log(msg):
    stamp = datetime.datetime.now(PARIS).strftime("%Y-%m-%d %H:%M:%S")
    line = "[%s] %s" % (stamp, msg)
    print(line)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def notify(title, message):
    """Notification macOS. Silencieuse si elle echoue."""
    script = 'display notification %s with title %s' % (
        applescript_str(message), applescript_str(title))
    try:
        subprocess.run(["osascript", "-"], input=script, text=True,
                       capture_output=True, timeout=20)
    except Exception:
        pass


def applescript_str(value):
    """Litteral chaine AppleScript. Les sauts de ligne bruts sont interdits
    dans un litteral AppleScript : ils doivent passer en \\n."""
    text = (str(value)
            .replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\r\n", "\\n")
            .replace("\n", "\\n")
            .replace("\r", "\\n")
            .replace("\t", "\\t"))
    return '"' + text + '"'


def run_applescript(script):
    proc = subprocess.run(["osascript", "-"], input=script, text=True,
                          capture_output=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "osascript a echoue")
    return proc.stdout


def abbreviate(title):
    low = title.lower()
    for needle, short in ABBREV:
        if needle in low:
            return short
    # Repli : on garde le nom, nettoye des suffixes m/m, y/y, q/q
    cleaned = title
    for suffix in (" m/m", " y/y", " q/q", " (MoM)", " (YoY)"):
        cleaned = cleaned.replace(suffix, "")
    return cleaned.strip()


def priority_rank(short_title):
    try:
        return PRIORITY.index(short_title)
    except ValueError:
        return len(PRIORITY)


# --------------------------------------------------------------------------
# Recuperation et filtrage
# --------------------------------------------------------------------------

def fetch_feed():
    req = urllib.request.Request(FEED_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def target_week(now_paris):
    """Lundi -> vendredi vises. Dimanche = la semaine qui commence demain."""
    today = now_paris.date()
    if today.weekday() == 6:            # dimanche
        monday = today + datetime.timedelta(days=1)
    else:
        monday = today - datetime.timedelta(days=today.weekday())
    return monday, monday + datetime.timedelta(days=4)


def build_targets(feed, now_paris):
    """Retourne la liste des evenements a avoir dans Calendar."""
    monday, friday = target_week(now_paris)
    news, holidays = {}, []

    for raw in feed:
        if raw.get("country") not in CURRENCIES:
            continue
        impact = raw.get("impact")
        if impact not in ("High", "Holiday"):
            continue

        when = datetime.datetime.fromisoformat(raw["date"]).astimezone(PARIS)
        if not (monday <= when.date() <= friday):
            continue

        # L'HEURE FAIT PARTIE DE L'IDENTITE. Corrige le 29/08/2026.
        # Avec la date seule, une news deplacee dans la journee gardait la
        # meme cle, donc la meme empreinte de semaine, et le script sortait
        # sur « deja a jour » sans jamais ouvrir Calendar : le rendez-vous
        # restait a l'ancienne heure, indefiniment et en silence.
        # Avec l'heure, un decalage produit une cle NEUVE : l'ancienne n'est
        # plus voulue, REMOVE_STALE la supprime, et la nouvelle est creee.
        # Un seul evenement, a la bonne heure — jamais deux.
        ident = "%s|%s|%s" % (raw["country"], raw["title"], when.isoformat())

        if impact == "Holiday":
            holidays.append({
                "kind": "holiday",
                "currency": raw["country"],
                "date": when.date(),
                "title": "Holiday - %s" % raw["country"],
                "ids": [ident],
            })
        else:
            # Fusion : meme devise, meme horaire exact
            bucket = news.setdefault((raw["country"], when), [])
            bucket.append((abbreviate(raw["title"]), ident))

    targets = []
    for (currency, when), members in news.items():
        members.sort(key=lambda m: (priority_rank(m[0]), m[0]))
        seen, parts = set(), []
        for short, _ in members:
            if short not in seen:
                seen.add(short)
                parts.append(short)
        targets.append({
            "kind": "news",
            "currency": currency,
            "when": when,
            "title": "%s - %s" % (currency, " / ".join(parts)),
            "ids": sorted(m[1] for m in members),
        })

    # Deduplique les holidays (plusieurs lignes feriees le meme jour/devise)
    by_day = {}
    for hol in holidays:
        by_day.setdefault((hol["currency"], hol["date"]), hol)
    targets.extend(by_day.values())

    for tgt in targets:
        tgt["key"] = hashlib.sha1("||".join(tgt["ids"]).encode("utf-8")).hexdigest()[:16]
        tgt["sort"] = tgt["when"] if tgt["kind"] == "news" else \
            datetime.datetime.combine(tgt["date"], datetime.time(0), PARIS)
        # Un holiday reste "a creer" jusqu'a la fin de sa journee.
        tgt["future"] = tgt["sort"] >= now_paris if tgt["kind"] == "news" \
            else tgt["date"] >= now_paris.date()

    targets.sort(key=lambda t: t["sort"])
    return targets, monday, friday


# --------------------------------------------------------------------------
# Etat local (fast-path)
# --------------------------------------------------------------------------

def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
    os.replace(tmp, STATE_FILE)


# --------------------------------------------------------------------------
# AppleScript
# --------------------------------------------------------------------------

AS_HEADER = """
on monthOf(n)
\tset ms to {January, February, March, April, May, June, July, August, September, October, November, December}
\treturn item n of ms
end monthOf

on mkDate(y, m, d, hh, mm)
\tset t to current date
\tset time of t to 0
\tset day of t to 1
\tset year of t to y
\tset month of t to my monthOf(m)
\tset day of t to d
\tset time of t to (hh * hours + mm * minutes)
\treturn t
end mkDate
"""


def as_date(dt):
    return "my mkDate(%d, %d, %d, %d, %d)" % (dt.year, dt.month, dt.day,
                                              getattr(dt, "hour", 0),
                                              getattr(dt, "minute", 0))


def list_calendars():
    script = ('tell application "Calendar"\n\tset outp to ""\n'
              '\trepeat with c in calendars\n'
              '\t\tset outp to outp & (name of c) & linefeed\n'
              '\tend repeat\nend tell\nreturn outp')
    return [line.rstrip("\r") for line in run_applescript(script).split("\n")
            if line.strip()]


def match_calendar(names, wanted):
    """Nom exact d'abord, sinon comparaison sans espaces ni casse."""
    for name in names:
        if name == wanted:
            return name
    target = wanted.strip().casefold()
    for name in names:
        if name.strip().casefold() == target:
            return name
    return None


def resolve_calendars():
    """Renseigne CALS. Renvoie la liste des calendriers manquants."""
    names = list_calendars()
    missing = []
    for slot, wanted in (("news", CAL_NEWS), ("holiday", CAL_HOLIDAY)):
        found = match_calendar(names, wanted)
        if found is None:
            missing.append(wanted)
            CALS[slot] = None
        else:
            CALS[slot] = found
            if found != wanted:
                log("Calendrier %r resolu en %r" % (wanted, found))
    return missing


def read_existing(monday, friday, now_paris):
    """ff-key deja presentes sur la fenetre visee -> {cle: (calendrier, futur?)}."""
    start = datetime.datetime.combine(monday, datetime.time(0), PARIS)
    end = datetime.datetime.combine(friday + datetime.timedelta(days=1),
                                    datetime.time(0), PARIS)
    lines = [AS_HEADER, 'set outp to ""',
             "set winStart to %s" % as_date(start),
             "set winEnd to %s" % as_date(end),
             "set nowRef to %s" % as_date(now_paris),
             'tell application "Calendar"']
    for cal in (CALS["news"], CALS["holiday"]):
        if cal is None:
            continue
        lines.append("\ttry")
        lines.append("\t\ttell calendar %s" % applescript_str(cal))
        lines.append("\t\t\tset evs to (every event whose start date "
                     "≥ winStart and start date < winEnd)")
        lines.append("\t\t\trepeat with ev in evs")
        lines.append("\t\t\t\ttry")   # une note malformee ne doit pas tout stopper
        lines.append("\t\t\t\tset dsc to description of ev")
        lines.append("\t\t\t\tif dsc is not missing value then")
        lines.append('\t\t\t\t\tif dsc contains "ff-key:" then')
        # La cle fait toujours 16 caracteres hexa juste apres "ff-key:".
        # Extraction par longueur fixe : insensible au type de saut de ligne
        # que Calendar peut reinjecter dans les notes.
        lines.append('\t\t\t\t\t\tset AppleScript\'s text item delimiters to "ff-key:"')
        lines.append("\t\t\t\t\t\tset tailPart to text item 2 of dsc")
        lines.append('\t\t\t\t\t\tset AppleScript\'s text item delimiters to ""')
        lines.append("\t\t\t\t\t\tset kk to text 1 thru 16 of tailPart")
        lines.append("\t\t\t\t\t\tif (start date of ev) ≥ nowRef then")
        lines.append('\t\t\t\t\t\t\tset flg to "F"')
        lines.append("\t\t\t\t\t\telse")
        lines.append('\t\t\t\t\t\t\tset flg to "P"')
        lines.append("\t\t\t\t\t\tend if")
        lines.append('\t\t\t\t\t\tset outp to outp & kk & tab & flg & tab & %s & linefeed'
                     % applescript_str(cal))
        lines.append("\t\t\t\t\tend if")
        lines.append("\t\t\t\tend if")
        lines.append("\t\t\t\tend try")
        lines.append("\t\t\tend repeat")
        lines.append("\t\tend tell")
        lines.append("\ton error errText")
        lines.append('\t\tset outp to outp & "CALERROR " & errText & linefeed')
        lines.append("\tend try")
    lines.append("end tell")
    lines.append("return outp")

    out = run_applescript("\n".join(lines))
    found, errors = {}, []
    for line in out.splitlines():
        line = line.rstrip("\r\n")
        if not line.strip():
            continue
        if line.startswith("CALERROR"):
            errors.append(line)
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        found[parts[0].strip()] = (parts[2].strip(), parts[1].strip() == "F")
    if errors:
        raise RuntimeError("; ".join(errors))
    return found


def write_events(to_create, to_delete):
    lines = [AS_HEADER, 'tell application "Calendar"']

    for cal, key in to_delete:
        lines.append("\ttry")
        lines.append("\t\ttell calendar %s" % applescript_str(cal))
        lines.append("\t\t\tset doomed to (every event whose description "
                     "contains %s)" % applescript_str("ff-key:" + key))
        lines.append("\t\t\trepeat with i from (count of doomed) to 1 by -1")
        lines.append("\t\t\t\tdelete (item i of doomed)")
        lines.append("\t\t\tend repeat")
        lines.append("\t\tend tell")
        lines.append("\tend try")

    for tgt in to_create:
        note = "ff-key:%s\n%s" % (tgt["key"], "\n".join(tgt["ids"]))
        if tgt["kind"] == "news":
            start = tgt["when"]
            lines.append("\ttell calendar %s" % applescript_str(CALS["news"]))
            lines.append("\t\tset d to %s" % as_date(start))
            lines.append("\t\tset ev to make new event with properties "
                         "{summary:%s, start date:d, end date:(d + 15 * minutes), "
                         "description:%s}" % (applescript_str(tgt["title"]),
                                              applescript_str(note)))
            lines.append("\t\ttell ev to make new display alarm at end "
                         "with properties {trigger interval:0}")
            lines.append("\tend tell")
        else:
            day = datetime.datetime.combine(tgt["date"], datetime.time(0), PARIS)
            lines.append("\ttell calendar %s" % applescript_str(CALS["holiday"]))
            lines.append("\t\tset d to %s" % as_date(day))
            lines.append("\t\tset ev to make new event with properties "
                         "{summary:%s, start date:d, end date:d, allday event:true, "
                         "description:%s}" % (applescript_str(tgt["title"]),
                                              applescript_str(note)))
            lines.append("\t\ttell ev to make new display alarm at end "
                         "with properties {trigger interval:-240}")
            lines.append("\t\ttell ev to make new display alarm at end "
                         "with properties {trigger interval:540}")
            lines.append("\tend tell")

    lines.append("end tell")
    lines.append('return "OK"')
    run_applescript("\n".join(lines))


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    force = "--force" in sys.argv
    dry = "--dry-run" in sys.argv
    now = datetime.datetime.now(PARIS)

    try:
        feed = fetch_feed()
    except Exception as exc:
        log("ECHEC feed : %s" % exc)
        notify("Forex Sync", "Feed injoignable : %s" % exc)
        return 1

    if not isinstance(feed, list) or not feed:
        log("ECHEC : feed vide ou format inattendu")
        notify("Forex Sync", "Feed vide ou format inattendu.")
        return 1

    targets, monday, friday = build_targets(feed, now)
    week_id = monday.isoformat()
    fingerprint = hashlib.sha1(
        "||".join(sorted(t["key"] for t in targets)).encode("utf-8")).hexdigest()

    state = load_state()
    entry = state.get(week_id, {})

    if not force and entry.get("fingerprint") == fingerprint and entry.get("applied"):
        log("Semaine %s deja a jour (%d evenements). Rien a faire."
            % (week_id, len(targets)))
        return 0

    try:
        resolve_calendars()
    except Exception as exc:
        log("ECHEC acces Calendar : %s" % exc)
        notify("Forex Sync", "Calendar inaccessible : %s" % exc)
        return 1

    if CALS["news"] is None:
        log("ECHEC : calendrier %r introuvable." % CAL_NEWS)
        notify("Forex Sync", "Calendrier %r introuvable." % CAL_NEWS)
        return 1
    if CALS["holiday"] is None:
        # Non bloquant : les news rouges restent creees.
        holiday_count = sum(1 for t in targets if t["kind"] == "holiday")
        log("ATTENTION : calendrier %r introuvable, %d jour(s) ferie(s) ignore(s)."
            % (CAL_HOLIDAY, holiday_count))
        if holiday_count:
            notify("Forex Sync", "Calendrier %r introuvable : jours feries ignores."
                   % CAL_HOLIDAY)
        targets = [t for t in targets if t["kind"] != "holiday"]

    try:
        existing = read_existing(monday, friday, now)
    except Exception as exc:
        log("ECHEC acces Calendar : %s" % exc)
        notify("Forex Sync", "Calendar inaccessible : %s" % exc)
        return 1

    wanted = {t["key"]: t for t in targets}
    # On ne cree que ce qui est encore a venir : un evenement deja passe
    # n'a plus d'interet, et on ne veut pas de notification retroactive.
    to_create = [t for k, t in wanted.items()
                 if k not in existing and t["future"]]

    # Obsoletes : nos propres evenements (marques ff-key), encore a venir,
    # qui ont disparu du feed ou dont l'horaire a ete revise.
    to_delete = []
    if REMOVE_STALE:
        for key, (cal, is_future) in existing.items():
            if key not in wanted and is_future:
                to_delete.append((cal, key))

    if dry:
        log("[dry-run] semaine %s -> %s" % (monday, friday))
        for tgt in targets:
            when = tgt["when"].strftime("%a %d/%m %H:%M") if tgt["kind"] == "news" \
                else tgt["date"].strftime("%a %d/%m all-day")
            if tgt["key"] in existing:
                mark = "ok   "
            elif tgt["future"]:
                mark = "NEW  "
            else:
                mark = "passe"
            log("  %s %s | %s" % (mark, when, tgt["title"]))
        log("[dry-run] obsoletes a retirer : %d" % len(to_delete))
        return 0

    if to_create or to_delete:
        try:
            write_events(to_create, to_delete)
        except Exception as exc:
            log("ECHEC ecriture Calendar : %s" % exc)
            notify("Forex Sync", "Creation impossible : %s" % exc)
            return 1

    for tgt in to_create:
        when = tgt["when"].strftime("%a %d/%m %H:%M") if tgt["kind"] == "news" \
            else tgt["date"].strftime("%a %d/%m all-day")
        log("  + %s | %s" % (when, tgt["title"]))

    state[week_id] = {"fingerprint": fingerprint, "applied": True,
                      "count": len(targets),
                      "updated": now.isoformat(timespec="seconds")}
    for old in [k for k in state if k < (monday - datetime.timedelta(days=60)).isoformat()]:
        del state[old]
    save_state(state)

    if to_create or to_delete:
        log("Semaine %s : %d cree(s), %d obsolete(s) retire(s), %d au total."
            % (week_id, len(to_create), len(to_delete), len(targets)))
        notify("Forex Sync", "%d evenement(s) ajoute(s) pour la semaine du %s."
               % (len(to_create), monday.strftime("%d/%m")))
    else:
        log("Semaine %s : rien a creer, %d evenement(s) deja en place."
            % (week_id, len(targets)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
