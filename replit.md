# Photography Gallery

This is a static HTML/CSS/JavaScript photography gallery with a small Python server for secure Cerebras image descriptions. The Cerebras API key stays server-side in the `CEREBRAS_API_KEY` secret.

## Run on Replit

Use the **Start application** workflow. It runs:

`python3 server.py`

The Replit web preview opens the site on port 5000. The Generate AI Description button sends the selected image to Cerebras through the server; the browser does not load a local captioning model or contain an API key. Generated descriptions are associated with stable image IDs and persisted in `descriptions.json`, so the button stays hidden for images that have already been described.