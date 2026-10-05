# Aucune révision

Le répertoire de migrations du greffon quand l'essai est **inactif** (`CHOREGOS_ESSAI_ONTOLOGIE`
absent) : `python -m choregos_api.migrer` y lit une branche vide, et la base n'a aucune table du
greffon. Actif, le point d'entrée désigne `../versions`.
