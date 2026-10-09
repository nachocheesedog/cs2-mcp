# ChatGPT gallery prompt (one prompt, all images in sequence)

The user attaches the reference renders in the order listed, then pastes this prompt. Fill in the `{…}` fields.
- `{KEEP}`: the asset's signature features, from the model not from memory. List 4–7 things a viewer would notice if
  they changed: silhouette, cladding colour and material, bracing or notches, crown, podium, companion buildings.
- `{SETTING}`: the real place if it's a real building (neighbourhood, city, nearby landmarks), otherwise "a dense,
  realistic downtown".

It usually works first try for real towers; naming nearby landmarks in the setting adds a nice touch.

```
The attached images are renders of the same 3D model of {BUILDING} ({WHAT IT IS}), each on a transparent
background. Create {N} separate images, one at a time, in order.

Rules for every image:
- Use the building exactly as it appears in the render: {KEEP}.
  Do not redraw, restyle, add or remove floors, or change its colours.
- Keep that render's camera angle and framing.
- Composite it into {SETTING}, where it clearly reads as the landmark: surrounding buildings of varied heights
  and materials, busy streets with traffic, trees along the sidewalks. Scale the neighbours correctly.
- Match the city's lighting, shadows and haze to the lighting already on the building.
- Photorealistic city-builder promotional screenshot, no text, no logos, no watermark.

Images:
1. First render ({VIEW 1}): {TIME/SKY 1}. 16:9.
2. Second render ({VIEW 2}): {TIME/SKY 2}. 16:9.
...
N. THUMBNAIL: using the same scene as image 1, make a SQUARE 1:1 version with the building centred and filling
   about 80% of the height, simple uncluttered background, strong clean lighting. It must read well when shown
   small (about 200 px).
```

**Fix-up line** when the model changes the building:
*"Image N changed the {BUILDING}. Redo image N and put back the building from the Nth render exactly; only change the
surroundings."*

**Game-look variant:** replace "Photorealistic city-builder promotional screenshot" with *"in the visual style of a
Cities: Skylines II in-game screenshot"*.

**Wide/low assets** (stations, parks): say "seen from above at a three-quarter angle". Tell the model what the empty
kerb outlines on the lot are ("fill the kerb outlines around it with lawns, trees and paved plazas"), and what's
underground or visible through glass.
