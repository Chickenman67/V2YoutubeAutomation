# PROTOTYPE - throws away the moment #5 resolves. Generates a topic-menu hero frame
# (2x2 grid of thumbnails) plus four full-size topic images for the camera excursion test.
# Layout constants below are hardcoded for Remotion's camera tween to match.
from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
MARGIN, GAP = 80, 40
CELL_W = (W - 2 * MARGIN - GAP) // 2
CELL_H = (H - 2 * MARGIN - GAP) // 2

TOPICS = [
    ("Treasury Yields", (20, 60, 120), (120, 200, 255)),
    ("Fed Rate Hikes", (90, 40, 110), (230, 120, 200)),
    ("CPI Inflation", (20, 90, 60), (90, 220, 160)),
    ("S&P 500 Rally", (110, 60, 20), (240, 180, 90)),
]

def topic_image(topic_title, c1, c2):
    img = Image.new("RGB", (W, H), c1)
    d = ImageDraw.Draw(img)
    for i in range(0, W, 60):
        alpha = int(255 * i / W)
        overlay = Image.new("RGB", (W, H), c2)
        img.paste(Image.blend(img, overlay, 0.0), (0, 0))
        band = Image.new("RGB", (60, H), c2)
        d.rectangle([i, 0, i + 60, H], fill=tuple(int(x) for x in c2))
    d.rectangle([0, 0, W - 1, H - 1], outline=(255, 255, 255), width=8)
    return img

def hero_frame():
    canvas = Image.new("RGB", (W, H), (240, 240, 240))
    d = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("arial.ttf", 54)
        small = ImageFont.truetype("arial.ttf", 30)
    except Exception:
        font = ImageFont.load_default()
        small = font
    for idx, (title, c1, c2) in enumerate(TOPICS):
        col = idx % 2
        row = idx // 2
        x = MARGIN + col * (CELL_W + GAP)
        y = MARGIN + row * (CELL_H + GAP)
        thumb = topic_image(title, c1, c2).resize((CELL_W, CELL_H))
        canvas.paste(thumb, (x, y))
        d.rectangle([x, y, x + CELL_W, y + CELL_H], outline=(0, 0, 0), width=4)
        label = f"{idx+1}. {title}"
        tw = d.textlength(label, font=font)
        d.rectangle([x, y + CELL_H - 80, x + CELL_W, y + CELL_H], fill=(20, 20, 20))
        d.text((x + (CELL_W - tw) // 2, y + CELL_H - 70), label, fill=(255, 255, 255), font=font)
    return canvas

for idx, (title, c1, c2) in enumerate(TOPICS):
    topic_image(title, c1, c2).save(f"public/assets/topic{idx}.png")

hero_frame().save("public/assets/hero.png")
print("hero.png + topic0..3.png written")