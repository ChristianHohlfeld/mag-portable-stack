# ROLLBACK – sicheres Zurück auf Q4

```bash
sudo bash ops/deepseek/hotswap.sh rollback
```

Falls IQ3 aktiv ist, fordert das Skript den **sequentiellen** Wechsel auf Q4 über den privilegierten Supervisor an: IQ3 stoppen und vollständig entladen, Q4 starten, /health und NUMA0 prüfen. **Erst nach gesundem Q4** wird das alte Gateway/Firewall-Snapshot aus `/var/lib/mag-llm/hotswap-rollback` wiederhergestellt. Ein fehlgeschlagener Q4-Restore führt zum sicheren Abbruch: kein Gateway, das fälschlich den entladenen Q4 als gesund ausgibt. Ist Q4 bereits gesund, erfolgt **kein Modellneustart**.

Nach erfolgreichem Rückweg wird die ursprüngliche IQ3-Startblockade aus #33 wiederhergestellt, die temporäre Q4-`RefuseManualStop`-Ausnahme auf ihren Vorgängerstand gebracht und der Supervisor gestoppt. Das dauerhaft deaktivierte `mag-model-pin` wird nicht wieder reaktiviert (das erledigt #33 bewusst nicht). Q4 behält die NUMA0-Bindung aus #33 und alle ursprünglichen GGUF-/Prompt-Cache-Einstellungen. Keine Chat-, Slot- oder Erinnerungsdateien werden gelöscht.

```bash
sudo systemctl is-active mag-llm.service
curl --noproxy '*' -fsS http://127.0.0.1:8090/health
curl --noproxy '*' -fsS http://127.0.0.1:8080/audit/v1/health
sudo nft list table inet mag_audit
```

Wichtig: `hotswap.sh apply` ist ein **Staging-Befehl, kein Modell-Neustart**. Ali führt den einmaligen geplanten Q4-Neustart ausschließlich als Schritt von #33 aus. Nur ein später ausdrücklich vom Owner ausgelöster Modellwechsel oder ein explizites Rollback kann Modelle wechseln.
