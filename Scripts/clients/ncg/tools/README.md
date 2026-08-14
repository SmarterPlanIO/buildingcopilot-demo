# Outils NCG — diagnostics et one-offs liés aux données NCG

Scripts écrits pendant les investigations NCG (debug retrieval, dédup sinistres,
purges ponctuelles, ancienne UI de démo `07_query_rag_ui.py`). Ils référencent en
dur la DB NCG (`sp-rag-ncg-copros`), des copros NCG (8050, 5390, ...) ou des cas
réels (extincteurs, dossiers nominatifs) : c'est assumé, ce sont des outils de
la mission NCG, pas du produit.

Règle de rangement : tout script sans raison technique d'être générique vit ici ;
`Scripts/` ne contient que le produit PALIM paramétré par `clients/<client>/client.json`.
Un nouveau client démarre avec un dossier `clients/<client>/tools/` vide.

Exécution : depuis `Scripts/` (les scripts qui importent `pipeline_config`,
comme `07_query_rag_ui.py`, ont besoin de `PYTHONPATH=.`) :

```bash
PYTHONIOENCODING=utf-8 PYTHONPATH=. python "clients/ncg/tools/<script>.py"
```
