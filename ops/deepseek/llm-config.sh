# shellcheck shell=bash
# Gemeinsame Konfiguration für die mag-llm-Unit (ab 3.6). Wird von warm.sh, install-deepseek.sh,
# upgrade-3.6.sh und tune.sh eingebunden:  source ops/deepseek/llm-config.sh
#
# Werte (Vorrang: Umgebung > /etc/mag-llm/llm.env > Standard):
#   LOAD_ARGS      Lademodus. Standard: --load-mode mmap+mlock
#                  (3.5-Messung: ohne mmap gleich schnell, aber nur 7,4 GiB gesperrt und kein schneller
#                  Neustart -> zurück auf mmap+mlock; die Gewichte SIND dann der gesperrte Page-Cache)
#   THREADS / THREADS_BATCH / BATCH / UBATCH   von tune.sh gemessen und hier gespeichert
#   BATCH = UBATCH: llama prüft einen Abbruch erst nach einem ganzen Batch – bei ~22 t/s dauert ein
#   2048er-Batch ~90 s, ein 1024er ~45 s. Größere Batches als ubatch bringen auf der CPU nichts.
# ab 3.8: ENGINE=llama|ik (Standard llama). ik = ik_llama.cpp (build-ik.sh) mit
#   IK_BIN (Pfad zu llama-server), IK_RTR=1|0 (Umsortieren für AVX2), IK_ARGS (z.B. -muge),
#   IK_ENV (mag-Varianten: MAG_NUMA_LOCAL=<threads> MAG_THP=1) – Unit über render-ik-unit.sh
# render_unit ZIEL|--check  erzeugt die Unit aus ops/mag-llm.service über render-llm-unit.sh
#   bzw. render-ik-unit.sh (ENGINE=ik) und prüft das Ergebnis.
MAG_LLM_ENV=${MAG_LLM_ENV:-/etc/mag-llm/llm.env}
_llm_src=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)

llm_config_load() {
  local _env_load=${LOAD_ARGS-} _env_t=${THREADS-} _env_tb=${THREADS_BATCH-} _env_b=${BATCH-} _env_ub=${UBATCH-}
  local _env_bin=${LLAMA_BIN-} _env_extra=${EXTRA_ARGS-} _env_na=${NUMA_ARGS-} _env_ln=${LLAMA_NUMA-}
  local _env_eng=${ENGINE-} _env_ikb=${IK_BIN-} _env_ikr=${IK_RTR-} _env_ika=${IK_ARGS-} _env_ike=${IK_ENV-}
  if [[ -r "$MAG_LLM_ENV" ]]; then
    # nur bekannte Schlüssel, keine Ausführung von Fremdinhalt
    while IFS='=' read -r k v; do
      v=${v%\"}; v=${v#\"}
      case "$k" in
        LOAD_ARGS) LOAD_ARGS=$v ;; THREADS) THREADS=$v ;; THREADS_BATCH) THREADS_BATCH=$v ;;
        BATCH) BATCH=$v ;; UBATCH) UBATCH=$v ;; LLAMA_BIN) LLAMA_BIN=$v ;; EXTRA_ARGS) EXTRA_ARGS=$v ;;
        NUMA_ARGS) NUMA_ARGS=$v ;; LLAMA_NUMA) LLAMA_NUMA=$v ;;
        ENGINE) ENGINE=$v ;; IK_BIN) IK_BIN=$v ;; IK_RTR) IK_RTR=$v ;; IK_ARGS) IK_ARGS=$v ;; IK_ENV) IK_ENV=$v ;;
      esac
    done < <(grep -E '^(LOAD_ARGS|THREADS|THREADS_BATCH|BATCH|UBATCH|LLAMA_BIN|EXTRA_ARGS|NUMA_ARGS|LLAMA_NUMA|ENGINE|IK_BIN|IK_RTR|IK_ARGS|IK_ENV)=' "$MAG_LLM_ENV")
  fi
  [[ -n "$_env_load" ]] && LOAD_ARGS=$_env_load
  [[ -n "$_env_t" ]] && THREADS=$_env_t
  [[ -n "$_env_tb" ]] && THREADS_BATCH=$_env_tb
  [[ -n "$_env_b" ]] && BATCH=$_env_b
  [[ -n "$_env_ub" ]] && UBATCH=$_env_ub
  [[ -n "$_env_bin" ]] && LLAMA_BIN=$_env_bin
  [[ -n "$_env_extra" ]] && EXTRA_ARGS=$_env_extra
  [[ -n "$_env_na" ]] && NUMA_ARGS=$_env_na
  [[ -n "$_env_ln" ]] && LLAMA_NUMA=$_env_ln
  [[ -n "$_env_eng" ]] && ENGINE=$_env_eng
  [[ -n "$_env_ikb" ]] && IK_BIN=$_env_ikb
  [[ -n "$_env_ikr" ]] && IK_RTR=$_env_ikr
  [[ -n "$_env_ika" ]] && IK_ARGS=$_env_ika
  [[ -n "$_env_ike" ]] && IK_ENV=$_env_ike
  # leerer Wert = Standard (mitgeliefertes Binary, keine Zusatzflags); "-" löscht einen gespeicherten Wert
  [[ "${LLAMA_BIN:-}" == - ]] && LLAMA_BIN=""
  [[ "${EXTRA_ARGS:-}" == - ]] && EXTRA_ARGS=""
  [[ "${NUMA_ARGS:-}" == - ]] && NUMA_ARGS=""
  [[ "${LLAMA_NUMA:-}" == - ]] && LLAMA_NUMA=""
  [[ "${ENGINE:-}" == - ]] && ENGINE=""
  [[ "${IK_BIN:-}" == - ]] && IK_BIN=""
  [[ "${IK_RTR:-}" == - ]] && IK_RTR=""
  [[ "${IK_ARGS:-}" == - ]] && IK_ARGS=""
  [[ "${IK_ENV:-}" == - ]] && IK_ENV=""
  LOAD_ARGS=${LOAD_ARGS:---load-mode mmap+mlock}
  THREADS=${THREADS:-28}; THREADS_BATCH=${THREADS_BATCH:-28}; UBATCH=${UBATCH:-1024}; BATCH=${BATCH:-$UBATCH}
  local x
  for x in "$THREADS" "$THREADS_BATCH" "$BATCH" "$UBATCH"; do
    [[ "$x" =~ ^[0-9]+$ ]] || { echo "llm-config: ungültiger Zahlenwert '$x'" >&2; return 1; }
  done
  (( UBATCH <= BATCH )) || { echo "llm-config: UBATCH ($UBATCH) > BATCH ($BATCH)" >&2; return 1; }
  LLAMA_BIN=${LLAMA_BIN:-}; EXTRA_ARGS=${EXTRA_ARGS:-}
  NUMA_ARGS=${NUMA_ARGS:---interleave=all}; LLAMA_NUMA=${LLAMA_NUMA:-distribute}
  ENGINE=${ENGINE:-llama}; IK_BIN=${IK_BIN:-}; IK_RTR=${IK_RTR:-1}; IK_ARGS=${IK_ARGS:-}; IK_ENV=${IK_ENV:-}
  [[ "$ENGINE" =~ ^(llama|ik)$ ]] || { echo "llm-config: ENGINE '$ENGINE' unbekannt" >&2; return 1; }
  [[ "$ENGINE" != ik || -n "$IK_BIN" ]] || { echo "llm-config: ENGINE=ik ohne IK_BIN" >&2; return 1; }
  export LOAD_ARGS THREADS THREADS_BATCH BATCH UBATCH LLAMA_BIN EXTRA_ARGS NUMA_ARGS LLAMA_NUMA ENGINE IK_BIN IK_RTR IK_ARGS IK_ENV
}

llm_config_save() {   # schreibt die aktuellen Werte nach $MAG_LLM_ENV
  install -d -m 0755 "$(dirname "$MAG_LLM_ENV")"
  local tmp; tmp=$(mktemp "$(dirname "$MAG_LLM_ENV")/.llm.env.XXXXXX")
  {
    echo "# mag-llm Startwerte – geschrieben von ${0##*/} am $(date -Is)"
    echo "LOAD_ARGS=\"$LOAD_ARGS\""
    echo "THREADS=$THREADS"; echo "THREADS_BATCH=$THREADS_BATCH"; echo "BATCH=$BATCH"; echo "UBATCH=$UBATCH"
    [[ -n "${LLAMA_BIN:-}" ]] && echo "LLAMA_BIN=$LLAMA_BIN"
    [[ -n "${EXTRA_ARGS:-}" ]] && echo "EXTRA_ARGS=\"$EXTRA_ARGS\""
    [[ "${NUMA_ARGS:---interleave=all}" != "--interleave=all" ]] && echo "NUMA_ARGS=\"$NUMA_ARGS\""
    [[ "${LLAMA_NUMA:-distribute}" != distribute ]] && echo "LLAMA_NUMA=$LLAMA_NUMA"
    [[ "${ENGINE:-llama}" != llama ]] && echo "ENGINE=$ENGINE"
    [[ -n "${IK_BIN:-}" ]] && echo "IK_BIN=$IK_BIN"
    [[ "${IK_RTR:-1}" != 1 ]] && echo "IK_RTR=$IK_RTR"
    [[ -n "${IK_ARGS:-}" ]] && echo "IK_ARGS=\"$IK_ARGS\""
    [[ -n "${IK_ENV:-}" ]] && echo "IK_ENV=\"$IK_ENV\""
    true
  } > "$tmp"
  chmod 0644 "$tmp"; mv -f "$tmp" "$MAG_LLM_ENV"
}

render_unit() {   # $1 = Ziel oder --check
  llm_config_load || return 1
  local tmp; tmp=$(mktemp /tmp/mag-llm-render.XXXXXX)
  if [[ "$ENGINE" == ik ]]; then
    bash "$_llm_src/ops/deepseek/render-ik-unit.sh" "$_llm_src/ops/mag-llm.service" "$tmp" || { rm -f "$tmp"; return 1; }
    local want wants=("--threads $THREADS " "--threads-batch $THREADS_BATCH " "--batch-size $BATCH " "--ubatch-size $UBATCH "
                      "numactl $NUMA_ARGS $IK_BIN " "--numa $LLAMA_NUMA " "--parallel 1 ")
    [[ "$IK_RTR" == 1 ]] && wants+=("  -rtr")
    local kv; for kv in $IK_ENV; do wants+=("Environment=$kv"); done
    for want in "${wants[@]}"; do
      grep -qF -- "$want" "$tmp" || { echo "render_unit: '$want' fehlt in der erzeugten ik-Unit" >&2; rm -f "$tmp"; return 1; }
    done
    if [[ "$1" == --check ]]; then
      echo "render_unit: ok (ik, threads $THREADS/$THREADS_BATCH, batch $BATCH/$UBATCH, rtr $IK_RTR${IK_ENV:+, $IK_ENV})" >&2; rm -f "$tmp"; return 0
    fi
    install -m 0644 "$tmp" "$1"; rm -f "$tmp"; return 0
  fi
  # render-llm-unit.sh setzt Lademodus und Zahlen ein (Werte kommen per Umgebung)
  MAG_LLM_ENV="$MAG_LLM_ENV" bash "$_llm_src/ops/deepseek/render-llm-unit.sh" "$_llm_src/ops/mag-llm.service" "$tmp" || { rm -f "$tmp"; return 1; }
  local want
  local wants=("--threads $THREADS " "--threads-batch $THREADS_BATCH " "--batch-size $BATCH " "--ubatch-size $UBATCH " "$LOAD_ARGS ")
  [[ -n "$LLAMA_BIN" ]] && wants+=(" $LLAMA_BIN serve ")
  [[ -n "$EXTRA_ARGS" ]] && wants+=("  $EXTRA_ARGS")
  wants+=("numactl $NUMA_ARGS " "--numa $LLAMA_NUMA ")
  for want in "${wants[@]}"; do
    grep -qF -- "$want" "$tmp" || { echo "render_unit: '$want' fehlt in der erzeugten Unit" >&2; rm -f "$tmp"; return 1; }
  done
  if [[ "$1" == --check ]]; then
    echo "render_unit: ok ($LOAD_ARGS, threads $THREADS/$THREADS_BATCH, batch $BATCH/$UBATCH)" >&2; rm -f "$tmp"; return 0
  fi
  install -m 0644 "$tmp" "$1"; rm -f "$tmp"
}
