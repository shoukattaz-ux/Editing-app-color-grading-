"""Entry point of the frozen engine (PyInstaller): the same local server as `python -m evp.server`."""
import multiprocessing
from evp.server import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
