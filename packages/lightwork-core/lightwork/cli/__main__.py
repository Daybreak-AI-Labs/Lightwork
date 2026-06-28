"""Enable ``python -m lightwork.cli`` now that ``cli`` is a package."""
from . import main

if __name__ == "__main__":
    main()
