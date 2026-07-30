# CLAUDE.md

Notes d'architecture pour travailler sur SnapSort. À lire avant de modifier le code.

## Ce qu'est le projet

Une application web locale qui trie les souvenirs d'un export Snapchat façon
Tinder : un souvenir à l'écran, une touche, le suivant. Backend Python
(bibliothèque standard uniquement), frontend HTML/CSS/JS sans build.

**Contrainte forte : zéro dépendance.** N'ajoute ni `pip install`, ni npm, ni
étape de compilation. Le projet doit se lancer par `python3 snapsort.py` sur une
machine neuve. Python 3.9 est le minimum (les annotations utilisent
`from __future__ import annotations`).

## Invariants à ne jamais casser

1. **Les sources ne sont jamais modifiées.** Aucun `os.remove`, `shutil.move` ni
   écriture sur un chemin source. « Supprimer » signifie « ne pas copier ». La
   seule exception est l'option `mode: "move"`, réservée aux sources dossier, et
   qui déplace vers la destination — jamais vers la corbeille système.
2. **Rien ne sort de la machine.** Le serveur écoute sur `127.0.0.1` uniquement.
   Pas d'appel réseau, pas de télémétrie, pas de CDN. Les liens OpenStreetMap
   sont de simples `<a target="_blank">` que l'utilisateur clique s'il veut.
3. **Les 22 Go ne sont jamais décompressés d'un bloc.** Toute nouvelle
   fonctionnalité qui a besoin d'un média passe par `MediaCache.path_for()`.
4. **`_undo_files` ne supprime que dans la destination.** Toute modification de
   cette fonction doit conserver la vérification `commonpath` avec `self.dest`.
5. **L'index n'est écrit qu'une fois.** Ne remets pas `items`/`order` dans
   `state.json` : c'est 2,6 Mo réécrits à chaque swipe (bug corrigé, ne pas
   régresser). Voir « Persistance » plus bas.

## Structure

```text
snapsort.py              lanceur (python3 snapsort.py)
snapsort/
  __main__.py            arguments CLI, mode autostart
  scan.py                détection des sources, index, métadonnées
  session.py             état du tri, cache média, file de copie
  server.py              serveur HTTP, API JSON, streaming Range
  web/index.html         les 4 écrans + les modales
  web/style.css          tout le style (un seul fichier)
  web/app.js             tout le comportement (un seul fichier, sans framework)
```

## Comment les données Snapchat sont structurées

Un export = plusieurs ZIP de ~2 Go. Le **premier seulement** contient
`json/memories_history.json` et `html/` ; tous contiennent `memories/`.

Nommage : `YYYY-MM-DD_<uuid>-main.{jpg,mp4}` avec un `-overlay.png` optionnel
(le texte et les dessins ajoutés dans l'app, dans un fichier séparé).

`memories_history.json` donne `Date` (UTC), `Media Type`, `Location`
— **mais aucun nom de fichier**. Le lien se fait par l'horodatage : la date de
modification d'une entrée ZIP *est* l'instant UTC du souvenir, à la granularité
2 s du format ZIP. C'est ce que fait `scan._attach_metadata` (5 879/5 880 sur un
export réel). Si tu touches à cette fonction, vérifie le taux de correspondance.

Attention au fuseau : `info.date_time` d'un ZIP est de l'UTC naïf, alors que le
`mtime` d'un fichier décompressé par `unzip` est de l'heure locale.
`_scan_zip` et `_scan_dir` traitent ces deux cas différemment — c'est voulu.

## Concepts

**Item** — un souvenir. `id` = `<date>_<uuid>` (stable, sert de clé partout).
Porte `container` (le ZIP) + `entry` (le chemin interne), ou `entry` seul pour
une source dossier.

**Décision** — `keep` | `trash` | `fav` | `skip` | `folder`. Redécider un
souvenir déjà décidé supprime d'abord les fichiers produits par la décision
précédente (`_undo_files`), puis recopie. `_recount()` recalcule les compteurs de
tous les dossiers : appelle-le après toute mutation des décisions.

**File d'attente** — `Session.queue(start, count)` renvoie les prochains
souvenirs **non encore décidés** à partir de `start`, chacun avec son index
absolu, plus la position de reprise du balayage. C'est ce qui garantit qu'un
souvenir trié ne réapparaît jamais, y compris après une annulation ou une reprise
de session. Le client garde une file locale de 8 cartes d'avance ; la carte du
dessus est `buffer[0]`.

**Copie** — un thread unique consomme une file (`_enqueue` / `_run_worker`).
L'interface n'attend jamais une copie. Les erreurs vont dans `_errors` et sont
exposées par `/api/session`, elles ne bloquent pas le tri. Le `mtime` du fichier
copié est mis à la date du souvenir.

## Persistance

Dans `<destination>/.snapsort/` :

| Fichier | Contenu | Fréquence d'écriture |
| --- | --- | --- |
| `index.json` | items + ordre + stats (~3 Mo) | une fois au scan, et si l'ordre change |
| `state.json` | décisions, dossiers, curseur, options | regroupée, ≤ 1×/s, et à l'arrêt |
| `journal.jsonl` | une ligne par action | à chaque action |
| `cache/` | médias extraits | purge LRU (`cache_gb`, 3 Go) |

`save_index()` pour le lourd, `save()` pour un écrit immédiat de l'état,
`touch()` pour un écrit regroupé (à utiliser dans les chemins chauds comme
`decide`).

**Aucune décision n'est perdue**, et c'est le fruit de trois mécanismes qui se
complètent — si tu touches à l'un, garde les autres :

1. `journal.jsonl` reçoit chaque action immédiatement.
2. `serve()` intercepte `SIGTERM`/`SIGHUP` (terminal fermé, `kill`) pour passer
   par le `finally` qui écrit l'état.
3. `load()` appelle `_recover(saved_at)`, qui rejoue les lignes du journal plus
   récentes que l'état écrit. C'est le filet en cas de `SIGKILL` ou de coupure de
   courant. `_journal()` se met en veille pendant ce rejeu (`_replaying`) pour ne
   pas dupliquer l'historique.

Vérifié : après un `kill -9` suivant 4 décisions non écrites, les 4 sont
restaurées à la reprise.

Supprimer `.snapsort/` remet le tri à zéro sans toucher aux fichiers rangés.

## API

Tout est en JSON, sauf les médias. Les erreurs renvoient `{"error": "…"}` avec un
code HTTP 4xx.

```text
GET  /api/bootstrap        suggestions de sources, session en cours, autostart
GET  /api/browse?path=     navigateur de fichiers côté serveur
GET  /api/session          snapshot complet (stats, compteurs, avancement du scan)
GET  /api/queue?start=&count=   prochains souvenirs non triés (+ prefetch)
GET  /api/media/{id}/{main|overlay}   binaire, supporte Range
GET  /api/report           génère et renvoie RAPPORT.md
POST /api/session/start    {sources[], dest, options} → lance le scan
POST /api/session/resume   {dest}
POST /api/decide           {id, action, folder?}
POST /api/undo             annule la dernière décision
POST /api/replay           {action} remet en file (par défaut les « passés »)
POST /api/folders          {name, key?} · /api/folders/update · /api/folders/delete
POST /api/options          {options}
POST /api/reveal           ouvre un dossier dans le Finder / l'explorateur
```

## Frontend

Pas de framework, pas d'outil de build. Trois objets globaux dans `app.js` :
`Setup` (assistant), `Sorter` (écran de tri), plus les modales `Browser` et
`Prompt`. Les écrans sont des `<section class="screen">` ; `showScreen(id)` en
active une.

Les cartes : `Sorter.render()` reconstruit les 3 cartes du dessus depuis
`buffer[0..2]`. Le dernier enfant de `#cards` est la carte du dessus (l'ordre DOM
fait la superposition). Le glisser-déposer est en Pointer Events dans
`attachDrag()`.

Le style est dans un seul fichier CSS, thème sombre assumé (les médias
ressortent mieux). Accent jaune Snapchat utilisé avec parcimonie. Les couleurs
d'action sont sémantiques : rouge = supprimer, vert = garder, jaune = favori.

## Tester

Il n'y a pas de suite de tests automatisés (pas de dépendances, et il faut un
export réel). Vérifie à la main avec un vrai export :

```bash
# scan + métadonnées
python3 -c "
import sys; sys.path.insert(0,'.')
from snapsort.scan import scan
r = scan(['<dossier des zips>'])
print(r['stats'])   # metadata_matched doit être ~= total
"

# bout en bout
python3 snapsort.py --port 8799 --no-browser --dest /tmp/out --source <dossier>
curl -s localhost:8799/api/session | python3 -m json.tool
```text

À vérifier après un changement : le taux de `metadata_matched`, qu'une décision
coûte ~2 ms (pas 68 ms — signe que l'index est réécrit), que la file ne
ré-affiche pas un souvenir trié, que les requêtes `Range` renvoient bien 206, et
que rien n'est écrit hors de la destination.

Pour une capture d'écran sans navigateur graphique :
`"/Applications/Brave Browser.app/Contents/MacOS/Brave Browser" --headless
--screenshot=out.png --window-size=1440,980 --virtual-time-budget=6000
http://127.0.0.1:8799/`

## Style

Code et commentaires en français, comme l'interface. Commentaires rares, pour
expliquer *pourquoi* (un piège, un choix non évident), jamais *quoi*. Lignes
≤ 100 caractères. Pas de `print` de débogage dans le code livré.

## Pistes non faites

- Graver les calques dans l'image (nécessiterait Pillow / ffmpeg → casse le
  « zéro dépendance » ; à faire en optionnel et dégradable si un jour)
- Trier aussi `chat_media/` (autre dossier de l'export, même approche)
- Détection de doublons (les rafales Snapchat en produisent beaucoup)
- Vue grille pour un survol rapide avant de trier
