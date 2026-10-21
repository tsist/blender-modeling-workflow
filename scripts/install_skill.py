"""Copy the one packaged skill into an explicit user-selected skill root."""
import argparse
from pathlib import Path
import shutil

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--destination', type=Path, required=True,
                    help='Absolute skill root; the blender-modeling-workflow child must not exist')
args = parser.parse_args()
if not args.destination.is_absolute():parser.error('Destination must be absolute')
root = Path(__file__).resolve().parents[1]
source = root / 'skills/blender-modeling-workflow'
destination = args.destination.resolve() / source.name
if destination.exists():parser.error('Skill already exists; compare or back it up before choosing a new destination')
for path in source.rglob('*'):
    if path.is_symlink() or getattr(path, 'is_junction', lambda:False)():
        parser.error('Packaged skill must not contain links')
shutil.copytree(source, destination)
print(str(destination))
