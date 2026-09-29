from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

def sample_font(family, style="Regular", characters="A"):
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder([".notdef", "A"])
    builder.setupCharacterMap({ord(char): "A" for char in characters})
    glyphs = {}
    for name in (".notdef", "A"):
        pen = TTGlyphPen(None)
        pen.moveTo((50, 0))
        pen.lineTo((250, 700))
        pen.lineTo((450, 0))
        pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (500, 0) for name in glyphs})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": family, "styleName": style,
                           "fullName": f"{family} {style}", "psName": f"{family}-{style}"})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    return builder.font

