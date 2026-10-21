# Earnings-21 chunks

Parsed transcripts for every call in the public Earnings-21 set. One parquet
file per call. Deploy embeds this whole directory and writes the vectors to
VastDB, so a later answer can cite any of these companies.

Source: the Earnings-21 transcripts in
[revdotcom/speech-datasets](https://github.com/revdotcom/speech-datasets).
No audio is included. The calls are from 2020.

License: [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).

44 calls, 3062 chunks. Regenerate with:

```bash
python -m temp_ingest.prepare_earnings21 --skip-download --out corpus/earnings21
```
