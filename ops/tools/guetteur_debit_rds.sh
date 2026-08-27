#!/bin/bash
# guetteur_debit_rds.sh — surveille le debit d'ecriture RDS d'un run de pipeline PALIM
# et alerte quand il s'effondre (saturation des credits IOPS sur t4g.micro).
#
# Ne dans l'incident du 27/08/2026 : lors du rattrapage dedup (29 copros rechargees
# dans la journee), le debit du 06b de la copro 5750 est passe de 495 a 1 chunk/s,
# puis le 06b a echoue apres 1h07, laissant la copro sans index BM25 et sans lignes
# dans `documents`. Personne n'a rien vu pendant plus d'une heure : le log restait
# muet et le format tqdm avait bascule de "it/s" a "s/it", ce qui rend l'effondrement
# illisible a l'oeil (1.98s/it = 0,5 chunk/s).
#
# Usage :
#   bash ops/tools/guetteur_debit_rds.sh <log> [seuil_chunks_par_s] [minutes_stall]
#   defauts : seuil=40, stall=5 minutes
#
# A brancher sur un Monitor Claude Code, ou a lancer en tache de fond a cote d'un
# gros run (ingest.py, 06b_load_db.py, rattrapage). Rappel exploitation : la cause
# racine est l'epuisement des credits burst ; etaler les gros re-runs sur plusieurs
# nuits suffit a l'eviter (decision du 28/08 : on reste en t4g.micro, NCG plafonne
# a 20-30 copros).

LOG="${1:?usage: guetteur_debit.sh <log> [seuil] [minutes_stall]}"
SEUIL="${2:-40}"
STALL_MIN="${3:-5}"

etat="init"; dernier_n=""; fige_depuis=0

debit_actuel() {
  local ligne
  ligne=$(tr '\r' '\n' < "$LOG" 2>/dev/null | grep -E "(it/s|s/it)\]" | tail -1)
  [ -z "$ligne" ] && { echo ""; return; }
  if [[ "$ligne" =~ ([0-9.]+)it/s ]]; then
    echo "${BASH_REMATCH[1]}"
  elif [[ "$ligne" =~ ([0-9.]+)s/it ]]; then
    awk -v s="${BASH_REMATCH[1]}" 'BEGIN{ if (s>0) printf "%.2f", 1/s; else print "0" }'
  else
    echo ""
  fi
}

compteur_actuel() {
  tr '\r' '\n' < "$LOG" 2>/dev/null | grep -oE "[0-9]+/[0-9]+ \[" | tail -1 | cut -d/ -f1
}

while true; do
  if tr '\r' '\n' < "$LOG" 2>/dev/null | grep -qE "TERMINEE|TERMINE|ECHEC|FATAL|Traceback"; then
    echo "[debit] run termine — arret du guetteur de debit"
    break
  fi

  d=$(debit_actuel)
  n=$(compteur_actuel)

  if [ -n "$d" ]; then
    sous=$(awk -v d="$d" -v s="$SEUIL" 'BEGIN{print (d<s)?1:0}')
    if [ "$sous" = "1" ] && [ "$etat" != "degrade" ]; then
      echo "[debit] ALERTE — chute a ${d} chunks/s (seuil ${SEUIL}) : saturation IOPS probable sur la RDS"
      etat="degrade"
    elif [ "$sous" = "0" ] && [ "$etat" = "degrade" ]; then
      echo "[debit] retabli — ${d} chunks/s"
      etat="ok"
    elif [ "$etat" = "init" ]; then
      etat=$([ "$sous" = "1" ] && echo degrade || echo ok)
      echo "[debit] demarrage a ${d} chunks/s (etat: ${etat})"
    fi
  fi

  # progression figee alors qu'une barre est active
  if [ -n "$n" ] && [ "$n" = "$dernier_n" ]; then
    fige_depuis=$((fige_depuis + 1))
    if [ "$fige_depuis" -eq "$STALL_MIN" ]; then
      echo "[debit] ALERTE — compteur fige a ${n} depuis ${STALL_MIN} min (barre active, aucune avancee)"
    fi
  else
    [ "$fige_depuis" -ge "$STALL_MIN" ] && echo "[debit] progression repartie (${n})"
    fige_depuis=0
  fi
  dernier_n="$n"

  sleep 60
done
