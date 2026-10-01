# Soak the Bag

A 45 to 75 second score-attack recipe sim of the garri ritual. You pour, you pick cold or warm water, you stir out the dry spots, you time the sugar, and then you eat. Charts fade. Garri stays.

## How to run

It is one static file with no build step and no backend.

- Open `index.html` in any modern browser, or
- Serve the folder: `python3 -m http.server -d soak-the-bag 8000`, then visit `http://localhost:8000`.

It is built for touch first and also works with a mouse. The hold buttons also respond to Space and Enter. The only network request is for Google Fonts, and the game falls back to system fonts if that request fails.

## The loop

| # | Phase | Input | What counts |
|---|-------|-------|-------------|
| 1 | Pour the garri (10s) | Hold the button or the bowl. The pour speeds up the longer you hold. | How full the bowl is against the 60–80% band. Above 100% it overflows onto the table. |
| 2 | Cold or warm (12s) | Pick a temperature, then hold to pour. | Water level against a band sized to your garri. Cold stops the moment you let go and has a wider band. Warm keeps flowing for a moment after release and has a narrower band. |
| 3 | Bowl + spoon. Mix. (15s cold / 12s warm) | Drag the spoon in circles. Taps also stir. | Pale, speckled dry spots must be stirred clear. Underpouring the water adds more spots. If the spoon stays outside the bowl for more than 1s you lose 2 points, up to 3 times. |
| 4 | Sugar is alpha (10s) | Tap to add a spoon of sugar, then Lock it in. | The sweet window drifts and shrinks over time. Sugar only goes up, so the move is to sit just under the window and lock when it reaches you. |
| 5 | Eat. Then ape. | – | The payoff animation, then the share card. |

When a phase timer runs out, whatever you have is locked in.

## Scoring

Each component is scored by distance from its target. Inside the band you keep 80–100% of the points depending on how centred you are. Outside the band the score falls off linearly from 80% to 0.

| Component | Max | Notes |
|-----------|-----|-------|
| Pour accuracy | 20 | Target 0.70 ± 0.10, falls to 0 by 0.30 outside the band |
| Water | 25 | Accuracy is worth 20. Temperature bonus: warm +5 or cold +4 when in the band, +2 when within twice the band |
| Mix | 30 | 82% from dry-spot coverage and 18% from how much of the bowl you stirred, minus spoon penalties |
| Sugar | 25 | Scored against the window at the moment you lock |
| DLC | +1 each | Groundnut, milk and ice. The total is capped at 100, and S rank is reachable without them |

Grades: **S** Soaked perfect (90+), **A** Sweet (78+), **B** Edible (62+), **C** Chalky (45+), **D** Soup or Dry.

The result line comes from your weakest component, for example "Unsoaked bag.", "You became the liquidity.", "Boring chart.", "Soup. Red candle energy." or "Dry spots still in the bag."

The best score is stored in `localStorage` on this device only. There are no accounts and no wallet.

Share text template:

```
I soaked the bag — S (93). Cold, sugar sweet. No milk no fear. $GARRI
```

## Share card

The end screen is a single 360px-max card with the bowl, grade, score, result line, tags and breakdown. It screenshots cleanly at phone width. The bowl on the card mirrors the live canvas, so the idle animation (steam for warm, a rim glint for cold) keeps running.

## Assets

Every asset is generated in code, so there are no image or audio files.

- **Bowl**: a 128×128 canvas drawn per pixel and scaled with `image-rendering: pixelated`. It has a blue enamel rim with chips and a white interior on an indigo tablecloth.
- **Garri**: a per-pixel field of grain noise and moisture. Dry granules are pale with dark gaps. Wet granules darken to deep cassava and close the gaps, and the pile swells as it soaks. Dry spots keep the pale, speckled texture until they are stirred, so they are not marked by colour alone.
- **Sprites**: a plaid woven sack, a pure water sachet for cold, a flask for warm, and a metal spoon with a brown handle, all pre-rendered pixel by pixel.
- **Particles**: falling granules, water, sugar crystals (which stay on the surface), table spills, and steam.
- **Toppings**: groundnut pieces, a milk swirl, and bobbing ice cubes.
- **Sound**: WebAudio synthesis for the granule crackle, water pour, spoon clink, sugar tick, and success and fail stings. It starts on first touch and can be toggled from the header.
- **Fonts**: Pixelify Sans for display and Atkinson Hyperlegible for body text, both from Google Fonts.

## Out of scope for v1

Wallet connect, token rewards, multiplayer, accounts, ads and external APIs are not included. Playing does not need a contract address.
