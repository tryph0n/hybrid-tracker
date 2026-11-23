# Architecture

1. Each source module in `src/tracker/` downloads one upstream dataset and maps
   it to the unified schema.
2. `python -m tracker.ingest` validates the result and stores it as
   `data/events/<source>.csv`, committed to the repository.
3. The Streamlit page `src/streamlit_app.py` reads these files and nothing else.
4. The pipeline refreshes the live sources every day and publishes the app.
   See the Pipeline section of the [home page](index.md).
