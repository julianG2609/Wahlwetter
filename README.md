# Raw snapshots

Verbatim, gzipped copies of what each source returned, one per observed change.
This branch shares no history with `main`, so cloning the code does not pull
this data.

Layout: `snapshots/<year>/<month>/<UTC timestamp>-<sha256 prefix>.json.gz`

The file name's hash is of the *uncompressed* bytes. Data here is from
dawum.de and is licensed ODbL; see `LICENSE-DATA` on `main`.
