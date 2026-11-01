"""Working with the owner's files. Nothing is overwritten, and every change except deleting can be undone."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .. import attachments, paths
from .registry import STRING, Plan, Tool, ToolRefused, UndoStack, text

MOST_LISTED = 200
MOST_FOUND = 20
MOST_SCANNED = 20_000
MOST_ORGANISED = 500
_NAMED_FOLDERS = ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos")
_OFF_LIMITS_NAMES = {"appdata", "windows", "program files", "program files (x86)", "programdata", ".ssh", "$recycle.bin"}
_PROJECT = Path(__file__).resolve().parent.parent.parent
_LEFT_IN_PLACE = {".lnk", ".url", ".ini", ".sys"}
_KINDS = {
    "Pictures": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".heic"},
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".hwp", ".ppt", ".pptx", ".xls", ".xlsx", ".csv"},
    "Videos": {".mp4", ".mkv", ".mov", ".avi", ".webm"},
    "Music": {".mp3", ".wav", ".flac", ".m4a", ".ogg"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz"},
    "Programs": {".exe", ".msi"},
}


def _kind_of(path: Path) -> str:
    return next((kind for kind, endings in _KINDS.items() if path.suffix.lower() in endings), "Other")


def _private_places() -> list[Path]:
    """Folders the owner has put off limits, one per line, in a file on this computer."""
    try:
        lines = (paths.data_dir() / "off_limits.txt").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    return [Path(line.strip()).resolve() for line in lines if line.strip()]


def _allowed(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if any(part.lower() in _OFF_LIMITS_NAMES for part in resolved.parts):
        raise ToolRefused("That folder is off limits.")
    if any(resolved == place or place in resolved.parents for place in (_PROJECT, paths.data_dir(), *_private_places())):
        raise ToolRefused("That folder is off limits.")
    return resolved


def _named(value: str) -> Path:
    """Lets the model say Desktop or Downloads without guessing the owner's user name."""
    first, _, rest = value.replace("\\", "/").partition("/")
    match = next((name for name in _NAMED_FOLDERS if name.lower() == first.lower()), None)
    return Path.home() / match / rest if match else Path(value)


def _path(arguments: dict, name: str) -> Path:
    path = _allowed(_named(text(arguments, name, 500)))
    if not path.is_absolute():
        raise ToolRefused("Use a full path, or start with Desktop, Documents or Downloads.")
    return path


def _existing(arguments: dict, name: str) -> Path:
    path = _path(arguments, name)
    if not path.exists():
        raise ToolRefused(f"{path.name} does not exist.")
    return path


def _free(target: Path) -> Path:
    if target.exists():
        raise ToolRefused(f"{target.name} already exists there, and APLHA does not overwrite files.")
    return target


def _folder(arguments: dict, name: str) -> Path:
    path = _existing(arguments, name)
    if not path.is_dir():
        raise ToolRefused(f"{path.name} is not a folder.")
    return path


def file_tools(computer, undo: UndoStack) -> list[Tool]:
    def list_folder(arguments):
        folder = _folder(arguments, "folder")
        entries = sorted(folder.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
        items = [{"name": item.name, "folder": item.is_dir()} for item in entries[:MOST_LISTED]]
        return {"ok": True, "path": str(folder), "items": items, "detail": f"Listed {len(items)} items in {folder.name}."}

    def find_files(arguments):
        wanted = text(arguments, "name", 100).lower()
        scanned, found = 0, []
        for root, folders, names in os.walk(_folder(arguments, "folder")):
            folders[:] = [item for item in folders if item.lower() not in _OFF_LIMITS_NAMES]
            scanned += len(names)
            found += [str(Path(root) / name) for name in names if wanted in name.lower()]
            if scanned > MOST_SCANNED or len(found) >= MOST_FOUND:
                break
        return {"ok": True, "found": found[:MOST_FOUND], "detail": f"Found {len(found[:MOST_FOUND])} matching files."}

    def read_file(arguments):
        path = _existing(arguments, "path")
        item = attachments.load(path)
        if item.is_photo:
            raise ToolRefused("That is a picture. Ask the owner to add it under Files and photos.")
        return {"ok": True, "untrusted_file_contents": item.text, "detail": f"Read {path.name}."}

    def plan_move(arguments):
        source = _existing(arguments, "source")
        target = _free(_folder(arguments, "destination_folder") / source.name)

        def perform():
            shutil.move(str(source), str(target))
            undo.push(f"{source.name} moved back", lambda: shutil.move(str(target), str(source)))
            return f"Moved {source.name} to {target.parent}."

        return Plan("move a file", f"{source} to {target.parent}", source.name, perform)

    def plan_rename(arguments):
        source = _existing(arguments, "path")
        new_name = text(arguments, "new_name", 200)
        if Path(new_name).name != new_name:
            raise ToolRefused("new_name must be a name only, with no folders.")
        target = _free(source.with_name(new_name))

        def perform():
            source.rename(target)
            undo.push(f"{new_name} renamed back", lambda: target.rename(source))
            return f"Renamed {source.name} to {new_name}."

        return Plan("rename a file", f"{source.name} to {new_name}", source.name, perform)

    def plan_copy(arguments):
        source = _existing(arguments, "source")
        if source.is_dir():
            raise ToolRefused("Only single files can be copied.")
        target = _free(_folder(arguments, "destination_folder") / source.name)

        def perform():
            shutil.copy2(source, target)
            undo.push(f"the copy of {source.name} removed", target.unlink)
            return f"Copied {source.name} to {target.parent}."

        return Plan("copy a file", f"{source} to {target.parent}", source.name, perform)

    def plan_new_folder(arguments):
        target = _free(_path(arguments, "path"))

        def perform():
            target.mkdir(parents=True)
            undo.push(f"the empty folder {target.name} removed", target.rmdir)
            return f"Created the folder {target}."

        return Plan("create a folder", str(target), target.name, perform)

    def plan_organise(arguments):
        folder = _folder(arguments, "folder")
        files = [
            item for item in folder.iterdir()
            if item.is_file() and item.suffix.lower() not in _LEFT_IN_PLACE and not item.name.startswith(".")
        ]
        if not files:
            raise ToolRefused("There are no loose files to organise.")
        if len(files) > MOST_ORGANISED:
            raise ToolRefused(f"That is more than {MOST_ORGANISED} files, so please choose a smaller folder.")

        def perform():
            moves, made = [], []
            for item in files:
                home = folder / _kind_of(item)
                if not home.exists():
                    home.mkdir()
                    made.append(home)
                target = home / item.name
                if target.exists():
                    continue
                shutil.move(str(item), str(target))
                moves.append((item, target))

            def put_back():
                for original, moved in reversed(moves):
                    shutil.move(str(moved), str(original))
                for home in made:
                    if not any(home.iterdir()):
                        home.rmdir()

            undo.push(f"{len(moves)} files in {folder.name} put back", put_back)
            return f"Organised {len(moves)} files in {folder.name} into folders by type."

        return Plan("organise a folder", str(folder), f"{len(files)} files", perform)

    def plan_delete(arguments):
        path = _existing(arguments, "path")

        def perform():
            computer.recycle(path)
            if path.exists():
                raise ToolRefused(f"{path.name} is still there.")
            return f"Moved {path.name} to the Recycle Bin."

        return Plan(f"delete: {path.name}", "It goes to the Recycle Bin, which APLHA cannot undo.", str(path), perform)

    source = {"source": (STRING, "The file. A full path, or starting with Desktop, Documents or Downloads.")}
    destination = {"destination_folder": (STRING, "The folder it goes to, which must exist.")}
    folder = {"folder": (STRING, "A full path, or Desktop, Documents, Downloads, Pictures, Music or Videos.")}
    path = {"path": (STRING, "A full path, or starting with Desktop, Documents or Downloads.")}
    return [
        Tool("list_folder", "List what is in a folder.", folder, ("folder",), run=list_folder),
        Tool("find_files", "Find files by part of their name.", {**folder, "name": (STRING, "Part of the name.")},
             ("folder", "name"), run=find_files),
        Tool("read_file", "Read a text file, to summarise it or answer questions.", path, ("path",), run=read_file),
        Tool("propose_move_file", "Move a file or folder.", {**source, **destination}, ("source", "destination_folder"),
             propose=plan_move),
        Tool("propose_rename_file", "Rename a file or folder.", {**path, "new_name": (STRING, "The new name only.")},
             ("path", "new_name"), propose=plan_rename),
        Tool("propose_copy_file", "Copy a file.", {**source, **destination}, ("source", "destination_folder"),
             propose=plan_copy),
        Tool("propose_create_folder", "Create a new folder.", path, ("path",), propose=plan_new_folder),
        Tool("propose_organise_folder", "Sort a folder's loose files into folders by type.", folder, ("folder",),
             propose=plan_organise),
        Tool("propose_delete_file", "Send a file to the Recycle Bin.", path, ("path",), propose=plan_delete),
    ]
