# Forex Sync

Les news à fort impact et les jours fériés **EUR / USD / JPY** de la semaine,
poussés dans l'app Calendar de macOS. Source : le flux JSON public de Forex
Factory. Aucune API, aucun compte, aucun robot qui clique.

*Dernière révision : 29/08/2026 — fusion des deux copies divergentes.*

---

## 1. En une minute

Un agent `launchd` lance le script **toutes les heures**. Il lit le flux, garde
ce qui est à fort impact sur les trois devises, et compare à ton calendrier :

- un événement **absent** est créé ;
- un événement qui a **changé d'heure** est déplacé — l'ancien est supprimé,
  le nouveau créé. Jamais deux ;
- un événement **disparu du flux** est retiré ;
- un événement **passé** n'est jamais touché.

Il ne touche que ce qu'il a lui-même posé : chaque événement porte une clé
`ff-key:` dans ses notes. Tes propres rendez-vous ne risquent rien.

## 2. Les trois fichiers, et lequel tourne

```
forex_sync.py     LA SOURCE. C'est ici qu'on modifie, et nulle part ailleurs
install.sh        copie la source vers ~/Library/Application Support/ForexSync/,
                  écrit le plist et recharge l'agent
forex-sync.sh     commande Raycast — lance la COPIE INSTALLÉE avec --force
```

⚠️ **Le fichier qui tourne n'est pas celui-ci.** C'est la copie installée dans
`~/Library/Application Support/ForexSync/forex_sync.py`, celle que le plist
appelle. Modifier la source ne change rien tant qu'`install.sh` n'a pas tourné.

*C'est exactement le piège du 29/08/2026 : la source avait 68 lignes de plus que
la copie installée — toute la résolution souple des noms de calendriers — parce
qu'elle avait été améliorée après l'installation et qu'`install.sh` n'avait
jamais été relancé. Les deux fichiers portaient la même date à la minute près.
Une divergence invisible à l'œil, et rien ne la signalait.*

**La règle : on modifie la source, on lance `install.sh`, point.**

```
bash install.sh
```

## 3. Vérifier que ça tourne

```
launchctl print gui/$(id -u)/com.local.forexsync
```

`runs`, `last exit code` et `state` disent tout. Les logs :

```
~/Library/Logs/forexsync.out.log     ce qu'il a fait, à chaque passage
~/Library/Logs/forexsync.err.log     vide = aucune erreur depuis l'installation
```

Un essai à blanc, qui n'écrit rien dans Calendar :

```
python3 forex_sync.py --dry-run
```

## 4. Les réglages

Tout en tête de `forex_sync.py` :

| | |
|---|---|
| `FEED_URL` | le flux Forex Factory de la semaine en cours |
| `CURRENCIES` | `EUR`, `USD`, `JPY` |
| impacts retenus | `High` et `Holiday` uniquement — codé dans `build_targets` |
| `CAL_NEWS` / `CAL_HOLIDAY` | les deux calendriers visés : `Trading News` et `Events` |
| `REMOVE_STALE` | `True` — retire ce qui n'est plus au programme |

La cadence est dans `install.sh` : `StartInterval 3600`, plus un passage au
démarrage et un filet le dimanche à 20 h. launchd ne rattrape **qu'un seul**
créneau au réveil, pas tous ceux tombés pendant le sommeil de la machine.

## 5. Décisions figées

**L'identité d'un événement porte l'HEURE, pas seulement la date.**
*(Corrigé le 29/08/2026.)* Avec la date seule, une news déplacée dans la journée
gardait la même clé, donc la même empreinte de semaine, et le script sortait sur
« déjà à jour » **sans jamais ouvrir Calendar**. Le rendez-vous restait à
l'ancienne heure, indéfiniment et en silence. La mécanique de déplacement
existait déjà et n'était jamais atteinte — c'était la clé qui était trop
grossière.

**Le flux JSON plutôt que le pilotage du navigateur.** L'approche précédente —
MCP, captures d'écran, contrôle de la souris — a été abandonnée : un flux public
bat un robot qui clique, et ça tient en un fichier.

**Les noms de calendriers se résolvent souplement.** Apple tolère une espace en
fin de nom (`"Events "`), AppleScript exige le nom exact. `resolve_calendars()`
compare sans casse ni espaces avant toute opération.

**On ne crée jamais rétroactivement.** Un événement déjà passé n'est pas créé —
pas de notification pour une news d'hier.
