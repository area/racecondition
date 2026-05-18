## VID/PID friendly-name lookup

Generate the lookup module used by the app from EEPROM manifests in
the `hexpansion-firmwares` submodule:

```bash
python3 scripts/generate_hexpansion_names.py
```

This writes `app/hexpansion_names.py`, which is imported by the app to
display a friendly device name when a hexpansion is detected.
