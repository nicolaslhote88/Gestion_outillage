# Playbook d'analyse SIGA

Objectif : transformer un brief Telegram parfois incomplet en fiches SIGA utiles, fiables et
exploitables en atelier. Ne pas seulement "classer des photos" : raisonner comme un gestionnaire
d'inventaire qui veut retrouver, utiliser, entretenir et relier le matériel.

## Sortie attendue avant écriture

Avant toute création ou modification, produire un plan de saisie et demander confirmation. Le plan
doit contenir :

- objets détectés, classés en équipement, accessoire ou consommable ;
- champs proposés : libellé, marque, modèle/référence, catégorie, état, emplacement, quantité,
  unité, stock minimum si utile, notes ;
- photos à archiver, rôle de chaque photo, photo principale proposée ;
- doublons ou fiches existantes trouvés ;
- liens proposés entre équipements, accessoires et consommables ;
- informations web retenues avec sources ;
- incertitudes et points à arbitrer.

## Identifier les objets

Utiliser ensemble le texte du brief, les photos et les inscriptions visibles. Distinguer :

- équipement : outil ou machine durable, généralement empruntable ou suivi individuellement ;
- accessoire : élément durable compatible avec un ou plusieurs équipements ;
- consommable : stock qui s'use, se remplace ou se compte en quantité.

Si un même dossier contient plusieurs objets, créer une proposition par objet et expliciter les
relations entre eux. Ne pas fusionner des objets différents parce qu'ils sont sur la même photo.

## Champs de fiche

Construire des libellés courts mais précis :

```text
<type> <marque> <modèle/référence> <caractéristique utile>
```

Exemples : `Perforateur Bosch GBH 2-26 DRE`, `Batterie Makita BL1850B 18V 5Ah`,
`Foret SDS-Plus Bosch Ø10 x 160 mm`.

Renseigner autant que possible :

- marque, modèle, référence fabricant, dimensions, tension, capacité, diamètre, longueur ;
- catégorie SIGA cohérente avec la base existante ;
- état visible : neuf, bon, usé, à vérifier, incomplet ;
- emplacement si le brief le donne ou si l'utilisateur le précise ;
- notes d'usage, compatibilité, entretien, stockage, sécurité, dépannage simple.

Si une information est inférée, la marquer comme telle dans le plan. Ne pas présenter une
inférence comme une certitude.

## Quantités et unités

Pour les consommables, toujours chercher une quantité et une unité.

Unités recommandées :

- `pcs` pour pièces comptables individuellement : forets, disques, lames, embouts, filtres ;
- `set` pour coffrets ou lots indivisibles ;
- `m` pour longueur, `kg` pour masse, `L` pour volume ;
- `box` seulement si la boîte est l'unité réellement gérée en stock.

Si la photo montre un lot mais que le nombre exact est incertain, proposer `stock_qty` estimé
avec une note `quantité à confirmer`, ou demander arbitrage avant écriture. Définir un
`stock_min_alert` quand c'est utile : consommable fréquent, critique, ou quantité faible.

## Photos

Chaque photo doit avoir une destination claire :

- `overview` : objet entier, utile comme photo principale ;
- `nameplate` : plaque signalétique, étiquette, référence, code-barres ;
- `detail` : état, défaut, compatibilité, connecteur, diamètre, usure.

Choisir la photo principale selon cet ordre :

1. vue nette de l'objet entier ;
2. objet isolé, peu encombré, reconnaissable en miniature ;
3. si le modèle/référence est plus important que la forme, photo `nameplate` nette ;
4. éviter une photo floue, sombre, redondante, ou contenant plusieurs objets sans contexte.

Archiver toutes les photos utiles, pas seulement la principale. Ne jamais attacher une photo à une
fiche si elle montre principalement un autre objet. Quand une photo contient plusieurs objets,
l'attacher seulement si elle apporte du contexte, sinon demander une séparation ou noter
l'ambiguïté.

## Recherche web

Faire des recherches web quand une marque, un modèle, une référence ou un consommable identifiable
apparaît. Prioriser :

- site fabricant, fiche produit, manuel PDF, vue éclatée, notice de sécurité ;
- distributeurs techniques fiables pour dimensions et compatibilités ;
- sources secondaires seulement pour compléter, jamais pour contredire le fabricant sans signaler
  l'incertitude.

Rechercher et intégrer, selon le cas :

- usage prévu, matériaux compatibles, limites d'utilisation ;
- accessoires compatibles, batteries/chargeurs, consommables associés ;
- dimensions importantes, tension, puissance, capacité, filetage, emmanchement ;
- stockage recommandé, entretien, nettoyage, pièces d'usure ;
- dépannage simple : symptômes fréquents, contrôles de base, pièces à vérifier ;
- EPI ou consignes de sécurité utiles.

Conserver les sources dans les notes de fiche quand les champs SIGA le permettent, sous une forme
courte : `Sources: fabricant URL; manuel URL`. Si les sources ne peuvent pas être écrites, les
inclure dans le compte rendu utilisateur.

## Liens équipement / accessoires / consommables

Créer ou proposer les liens seulement après vérification :

- accessoire compatible avec équipement : batterie, chargeur, flexible, coffret, guide, adaptateur ;
- consommable utilisé par équipement : foret, disque, lame, abrasif, filtre, buse, huile ;
- quantité par usage si connue : ex. `1 disque`, `2 filtres`, `1 lame`.

Classer mentalement chaque lien :

- certain : référence ou standard confirmé par photo, fiche fabricant ou base existante ;
- probable : cohérent avec la famille, mais pas prouvé ;
- à confirmer : information insuffisante.

Ne créer automatiquement que les liens confirmés par l'utilisateur. Dans le plan, séparer les
liens certains des liens probables.

## Doublons et idempotence

Toujours chercher avant de créer. Un doublon peut se cacher sous :

- libellé différent mais même modèle/référence ;
- faute d'orthographe ou marque abrégée ;
- accessoire déjà créé mais non lié à l'équipement ;
- consommable existant avec unité ou quantité différente.

Si le brief a déjà été traité ou si les photos sont déjà rattachées, ne pas recréer. Mettre à jour
ou compléter l'existant après confirmation.

## Qualité minimale

Ne pas écrire si :

- l'objet principal n'est pas identifiable ;
- la catégorie équipement/accessoire/consommable est incertaine et impacte la structure ;
- une photo risque d'être attachée au mauvais objet ;
- l'API ne fournit pas l'endpoint nécessaire ;
- les informations web trouvées ne correspondent pas clairement au modèle observé.

Dans ces cas, demander un arbitrage ou proposer une fiche `à vérifier` sans création immédiate.
