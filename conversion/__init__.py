"""PlayMySheetMusic conversion service: sheet music in, notes out.

    from conversion import convert
    result = convert([open("page.jpg", "rb").read()])
"""

__all__ = ["convert", "ConversionError"]


def __getattr__(name: str):
    # Loaded on first use, so the light parts (notes, webapi) can be used
    # without the image libraries installed.
    if name in __all__:
        from . import pipeline

        return getattr(pipeline, name)
    raise AttributeError(name)
