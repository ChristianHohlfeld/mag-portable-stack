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
# render_unit ZIEL|--check  erzeugt die Unit aus ops/mag-llm.service über render-llm-unit.sh und prüft das Ergebnis.
MAG_LLM_ENV=${MAG_LLM_ENV:-/etc/mag-llm/llm.env}
_llm_src=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)

llm_config_load() {
  local _env_load=${LOAD_ARGS-} _env_t=${THREADS-} _env_tb=${THREADS_BATCH-} _env_b=${BATCH-} _env_ub=${UBATCH-}
  local _env_bin=${LLAMA_BIN-} _env_extra=${EXTRA_ARGS-} _env_na=${NUMA_ARGS-} _env_ln=${LLAMA_NUMA-}
  if [[ -r "$MAG_LLM_ENV" ]]; then
    # nur bekannte Schlüssel, keine Ausführung von Fremdinhalt
    while IFS='=' read -r k v; do
      v=${v%\"}; v=${v#\"}
      case "$k" in
        LOAD_ARGS) LOAD_ARGS=$v ;; THREADS) THREADS=$v ;; THREADS_BATCH) THREADS_BATCH=$v ;;
        BATCH) BATCH=$v ;; UBATCH) UBATCH=$v ;; LLAMA_BIN) LLAMA_BIN=$v ;; EXTRA_ARGS) EXTRA_ARGS=$v ;;
        NUMA_ARGS) NUMA_ARGS=$v ;; LLAMA_NUMA) LLAMA_NUMA=$v ;;
      esac
    done < <(grep -E '^(LOAD_ARGS|THREADS|THREADS_BATCH|BATCH|UBATCH|LLAMA_BIN|EXTRA_ARGS|NUMA_ARGS|LLAMA_NUMA)=' "$MAG_LLM_ENV")
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
  # leerer Wert = Standard (mitgeliefertes Binary, keine Zusatzflags); "-" löscht einen gespeicherten Wert
  [[ "${LLAMA_BIN:-}" == - ]] && LLAMA_BIN=""
  [[ "${EXTRA_ARGS:-}" == - ]] && EXTRA_ARGS=""
  [[ "${NUMA_ARGS:-}" == - ]] && NUMA_ARGS=""
  [[ "${LLAMA_NUMA:-}" == - ]] && LLAMA_NUMA=""
  LOAD_ARGS=${LOAD_ARGS:---load-mode mmap+mlock}
  THREADS=${THREADS:-28}; THREADS_BATCH=${THREADS_BATCH:-28}; UBATCH=${UBATCH:-1024}; BATCH=${BATCH:-$UBATCH}
  local x
  for x in "$THREADS" "$THREADS_BATCH" "$BATCH" "$UBATCH"; do
    [[ "$x" =~ ^[0-9]+$ ]] || { echo "llm-config: ungültiger Zahlenwert '$x'" >&2; return 1; }
  done
  (( UBATCH <= BATCH )) || { echo "llm-config: UBATCH ($UBATCH) > BATCH ($BATCH)" >&2; return 1; }
  LLAMA_BIN=${LLAMA_BIN:-}; EXTRA_ARGS=${EXTRA_ARGS:-}
  NUMA_ARGS=${NUMA_ARGS:---interleave=all}; LLAMA_NUMA=${LLAMA_NUMA:-distribute}
  export LOAD_ARGS THREADS THREADS_BATCH BATCH UBATCH LLAMA_BIN EXTRA_ARGS NUMA_ARGS LLAMA_NUMA
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
    true
  } > "$tmp"
  chmod 0644 "$tmp"; mv -f "$tmp" "$MAG_LLM_ENV"
}

render_unit() {   # $1 = Ziel oder --check
  llm_config_load || return 1
  local tmp; tmp=$(mktemp /tmp/mag-llm-render.XXXXXX)
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
