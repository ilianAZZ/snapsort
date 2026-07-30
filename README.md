<div align="center">

# ✦ SnapSort

**Trie des milliers de souvenirs Snapchat à la vitesse d'un swipe.**

Un souvenir à l'écran, une touche, le suivant s'affiche. Gauche pour jeter,
droite pour garder, haut pour les favoris, un chiffre pour ranger dans un dossier.
C'est tout.

*Aucune dépendance · aucun compte · rien ne quitte ta machine.*

</div>

---

## Pourquoi

Quand on demande ses données à Snapchat, on reçoit une dizaine d'archives ZIP de
2 Go chacune, avec des milliers de fichiers nommés
`2019-07-14_a3f9c1d2-…-main.mp4`. Impossible de savoir ce qu'on garde sans tout
décompresser, et le Finder n'aide pas à trier 6 000 vidéos.

SnapSort lit les archives **sans les décompresser**, affiche chaque souvenir en
plein écran et n'attend de toi qu'un seul geste par souvenir. Ce que tu gardes
est copié dans un dossier propre, daté et organisé.

## Aperçu

```text
┌──────────────────────────────────────────────────────────────┐
│ ████████████░░░░░░░░░░░░  1 284 / 5 879 · reste 4 595   ↺ ? ✓│
├──────────────────────────────────────────────────────────────┤
│                                          ┌─────────────────┐ │
│                ┌──────────────┐          │ Samedi 14 juil. │ │
│                │              │          │ 2019            │ │
│                │   ▶ vidéo    │          │ Heure    18:32  │ │
│                │              │          │ Durée     0:09  │ │
│  SUPPRIMER     │   9 s · HD   │  GARDER  │ Défin. 1080×1920│ │
│                │              │          │ Poids   4,2 Mo  │ │
│                │              │          │ 📍 48.85, 2.35  │ │
│                └──────────────┘          └─────────────────┘ │
├──────────────────────────────────────────────────────────────┤
│      ← Supprimer   ↓ Passer   ↑ Favori   → Garder            │
│   ① Vacances 34   ② Famille 12   ③ Best of 5   ＋ Nouveau     │
└──────────────────────────────────────────────────────────────┘
```

## Installation

Il n'y a rien à installer. Juste **Python 3.9 ou plus récent**, déjà présent sur
macOS et Linux ([python.org](https://www.python.org/downloads/) sous Windows).

```bash
git clone https://github.com/<toi>/snapsort.git
cd snapsort
python3 snapsort.py
```

Le navigateur s'ouvre sur `http://127.0.0.1:8765`. Zéro dépendance à installer :
tout est écrit avec la bibliothèque standard de Python.

## Comment obtenir ses souvenirs Snapchat

1. Va sur [accounts.snapchat.com](https://accounts.snapchat.com) → **Mes données**
2. Demande ton export en cochant **« Inclure les fichiers de mes souvenirs »**
3. Snapchat envoie un mail avec des liens (compte quelques heures à quelques jours)
4. Télécharge **toutes** les archives `mydata~….zip` dans un même dossier
5. Lance SnapSort : il les détecte tout seul

> Les liens de téléchargement expirent au bout de 7 jours. Récupère tout d'un coup.

## Utilisation

### 1 · L'assistant (3 étapes)

SnapSort cherche les archives dans ton Bureau, tes Téléchargements et tes
Documents, et te les propose en un clic. Sinon, « Parcourir » te laisse choisir
un dossier ou cocher les archives à la main.

Tu choisis ensuite un **dossier de destination** — un dossier vide, ailleurs que
dans tes archives — puis tu valides. L'analyse des 22 Go prend moins d'une
seconde : SnapSort ne lit que le catalogue des archives.

### 2 · Le tri

| Touche | Action |
| :---: | --- |
| <kbd>←</kbd> | **Supprimer** — ne rien conserver |
| <kbd>→</kbd> | **Garder** — copié dans `Gardés/` |
| <kbd>↑</kbd> | **Favori** — copié dans `Favoris/` |
| <kbd>↓</kbd> | **Passer** — décider plus tard |
| <kbd>1</kbd>…<kbd>0</kbd> | Ranger dans le dossier associé à ce chiffre |
| <kbd>N</kbd> | Créer un dossier (une touche libre lui est attribuée) |
| <kbd>⌫</kbd> | Annuler la dernière décision |
| <kbd>Espace</kbd> | Lecture / pause · <kbd>M</kbd> son · <kbd>C</kbd> calques |
| <kbd>?</kbd> | Rappel des raccourcis |

Tu peux aussi **faire glisser la carte** à la souris ou au trackpad.

Le souvenir suivant est déjà préchargé : l'enchaînement est instantané. Les
copies se font en arrière-plan, tu ne les attends jamais.

**Créer un dossier** prend deux secondes : <kbd>N</kbd>, tu tapes « Vacances »,
la touche <kbd>1</kbd> lui est attribuée automatiquement. Ensuite un appui sur
<kbd>1</kbd> y envoie le souvenir affiché. Clic droit sur un dossier pour le
renommer ou changer sa touche.

### 3 · Le résultat

```text
MesSouvenirs/
├── Gardés/2019/2019-07-14_18h32m07s_a3f9c1.mp4
├── Favoris/2020/2020-08-17_22h45m44s_7bd104.jpg
├── Vacances/2021/…
├── RAPPORT.md            ← résumé du tri
└── .snapsort/            ← état de la session (supprimable)
```

Les fichiers sont renommés lisiblement et **datés à la date du souvenir**, pas à
celle de la copie : ils s'affichent dans le bon ordre dans le Finder et
s'importent correctement dans Photos.

## Ce que SnapSort ne fait pas

- **Il ne modifie ni ne supprime jamais tes archives source.** « Supprimer »
  veut simplement dire « ne pas copier ». Quand tu as fini, tu supprimes les ZIP
  toi-même — tu gardes la main.
- Il n'envoie rien sur Internet. Le serveur n'écoute que sur `127.0.0.1`. Le seul
  lien sortant est celui d'OpenStreetMap, si tu cliques sur les coordonnées GPS.
- Il ne « grave » pas les calques dans l'image. Les textes et dessins Snapchat
  sont dans des fichiers `-overlay.png` séparés : ils sont affichés par-dessus le
  média pendant le tri, et copiés à côté (`…-calque.png`) si tu gardes le souvenir.

## Reprendre plus tard

Ferme la fenêtre quand tu veux. Au prochain lancement, SnapSort propose de
reprendre là où tu t'étais arrêté. Les souvenirs déjà triés ne réapparaissent
jamais, même après une annulation.

Les souvenirs « passés » sont récupérables : à la fin du tri, le bouton
**« Revoir les passés »** les remet en file.

## Options

| Réglage | Par défaut | Autres valeurs |
| --- | --- | --- |
| Organisation | un sous-dossier par année | année + mois, ou tout à plat |
| Ordre de tri | du plus ancien au plus récent | du plus récent, ou aléatoire |
| Nom des fichiers | `2019-07-14_18h32m07s_a3f9c1` | nom Snapchat d'origine |
| « Supprimer » signifie | ne rien copier | copier dans `_Corbeille/` |
| Calques | copiés à côté du média | ignorés |

## En ligne de commande

```bash
python3 snapsort.py                              # assistant graphique
python3 snapsort.py --source ~/Desktop/snap --dest ~/MesSouvenirs
python3 snapsort.py --dest ~/MesSouvenirs        # reprend une session
python3 snapsort.py --port 9000 --no-browser
```

`--source` accepte un dossier d'archives, une archive précise (répétable) ou un
export déjà décompressé.

## Détails techniques

<details>
<summary>Pour les curieux</summary>

**Lecture des archives.** SnapSort ne lit que le *central directory* des ZIP :
5 879 souvenirs répartis sur 22 Go sont indexés en 0,8 s. Chaque média est
extrait au moment où il s'affiche (5 à 10 ms) vers un cache local purgé en LRU
(3 Go par défaut). Les 22 Go ne sont jamais décompressés d'un bloc.

**Métadonnées.** `json/memories_history.json` liste la date UTC, le type et les
coordonnées GPS de chaque souvenir, mais **sans nom de fichier**. SnapSort
retrouve la correspondance par l'horodatage : la date de modification stockée
dans le ZIP est exactement l'instant UTC du souvenir. Sur un export réel, 5 879
entrées sur 5 880 sont rattachées.

**Formats reconnus.** `YYYY-MM-DD_<uuid>-main.{jpg,mp4,…}` avec
`-overlay.png` optionnel, plus n'importe quel média en vrac dans un dossier.

**Persistance.** L'index (~3 Mo) est écrit une seule fois ; l'état du tri (petit)
est écrit au maximum une fois par seconde, et à l'arrêt. Chaque action est aussi
consignée dans `journal.jsonl`. Une décision par swipe coûte ~2 ms.

**Vidéos.** Servies avec support des requêtes HTTP `Range`, donc lecture et
déplacement dans la timeline fonctionnent normalement.

</details>

## Contribuer

Les idées et corrections sont bienvenues. [`CLAUDE.md`](CLAUDE.md) décrit
l'architecture, les invariants à respecter et comment tester — utile que tu
codes à la main ou avec un assistant.

## Licence

[MIT](LICENSE). Projet indépendant, sans aucun lien avec Snap Inc.
