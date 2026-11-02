"""Entry point: python -m mimir <cobot|watch|inspect|replicate|doctor>."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
