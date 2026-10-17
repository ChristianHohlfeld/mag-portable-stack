# Historical hot-swap documentation retired on 17 October 2026.

The Q4/IQ3 sequential model selector and model supervisor are disabled. Running both models must **never** require stopping or moving Q4.

For the replacement parallel NUMA1 instance and operational procedures, see [IQ3_PARALLEL.md](../IQ3_PARALLEL.md). The old `hotswap.sh` intentionally exits without modifying host state. The only supported staging entrypoint is `ops/deepseek/iq3-parallel.sh`.

Rollback of the new setup is `sudo bash ops/deepseek/iq3-parallel.sh rollback`. It never restarts Q4.
