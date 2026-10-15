"""Render the application's simple solar-panel mark at Windows icon sizes."""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SVG = '''<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
<rect width="256" height="256" rx="55" fill="#0b7771"/>
<g stroke="#ffd36b" stroke-width="6" stroke-linecap="round">
<path d="M174 29v9m0 70v9m-44-44h9m70 0h9m-75-31 7 7m48 48 7 7m0-62-7 7"/>
</g><circle cx="174" cy="73" r="25" fill="#ffd36b"/>
<path d="M57 119h121l26 71H31z" fill="#085b60" stroke="#f0fffb" stroke-width="7"
stroke-linejoin="round"/>
<path d="m97 119-8 71m48-71 9 71m-96-48h137m-146 24h155" fill="none"
stroke="#d7f5ec" stroke-width="4"/>
<path d="M118 192v24m-27 0h54" fill="none" stroke="#f0fffb" stroke-width="8"
stroke-linecap="round"/></svg>'''


def main():
    scale = 4
    canvas = Image.new("RGBA", (256 * scale, 256 * scale))
    draw = ImageDraw.Draw(canvas)
    def line(points, color, width):
        draw.line([(x * scale, y * scale) for x, y in points], fill=color,
                  width=width * scale, joint="curve")
    draw.rounded_rectangle((0, 0, 1023, 1023), radius=55 * scale, fill="#0b7771")
    for points in [[(174, 29), (174, 38)], [(174, 108), (174, 117)],
                   [(130, 73), (139, 73)], [(209, 73), (218, 73)],
                   [(143, 42), (150, 49)], [(198, 97), (205, 104)],
                   [(205, 42), (198, 49)]]:
        line(points, "#ffd36b", 6)
    draw.ellipse(tuple(v * scale for v in (149, 48, 199, 98)), fill="#ffd36b")
    panel = [(57, 119), (178, 119), (204, 190), (31, 190)]
    draw.polygon([(x * scale, y * scale) for x, y in panel], fill="#085b60")
    line(panel + [panel[0]], "#f0fffb", 7)
    for points in [[(97, 119), (89, 190)], [(137, 119), (146, 190)],
                   [(50, 142), (187, 142)], [(41, 166), (196, 166)]]:
        line(points, "#d7f5ec", 4)
    line([(118, 192), (118, 216)], "#f0fffb", 8)
    line([(91, 216), (145, 216)], "#f0fffb", 8)
    folder = ROOT / "static"
    (folder / "app-icon.svg").write_text(SVG, encoding="utf-8")
    icon = canvas.resize((256, 256), Image.Resampling.LANCZOS)
    icon.save(folder / "app-icon.png")
    icon.save(folder / "app-icon.ico", sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])
    print("Solar application icon generated")


if __name__ == "__main__":
    main()
