# Stetson Menu connector

Lets Claude (and the Stetson Plate tracker) look up The Eatery at Stetson East's
menu and nutrition live from Dine On Campus.

## 1. Check it works on your computer (2 min)

    pip install -r requirements.txt
    python server.py --probe

You should see Stetson East's location id, today's meals, and a few items.
If it says "Probe failed", copy the output back to Claude.

## 2. Put it online (free, ~5 min)

1. Make a new GitHub repo and upload these 4 files.
2. Go to render.com → New → Blueprint → pick the repo. It reads render.yaml.
3. When it's live, your connector URL is: https://<your-service>.onrender.com/mcp

Free Render services sleep when idle, so the first lookup after a while takes ~30 s.

## 3. Add it to Claude

Settings → Connectors → Add custom connector
Name: Stetson Menu    URL: https://<your-service>.onrender.com/mcp

Then tell Claude it's connected.

## Tools
- get_stetson_menu(date?, meal?) — stations and items with calories, protein,
  carbs, fat, fiber, sugar, sodium per portion
- list_stetson_meals(date?) — meal periods posted for a day
