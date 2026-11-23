#!/usr/bin/env python3
"""
tools/publish_release.py — публикация релиза с артефактами Android и iOS.

Схема из krasava-app, адаптирована под Flutter (APK/AAB + IPA).

Что делает:
  * stable  — создаёт/обновляет релиз по тегу, прикладывает APK, AAB и IPA;
  * beta/rc — то же, но помечает prerelease;
  * dev     — обновляет ОДИН rolling-релиз `preview` (артефакты заменяются,
              релизы не плодятся при каждом push в main).

Релиз-ноты генерируются из коммитов между предыдущим тегом и текущим, если
CHANGELOG.md для версии не найден.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def run(cmd: list[str], check: bool = True, capture: bool = True) -> str:
    result = subprocess.run(cmd, text=True, capture_output=capture)
    if check and result.returncode != 0:
        sys.exit(f"command failed: {' '.join(cmd)}\n{result.stderr}")
    return (result.stdout or "").strip()


def gh(*args: str, check: bool = True) -> str:
    return run(["gh", *args], check=check)


def upload_asset(tag: str, asset: str, repo: str, attempts: int = 3) -> None:
    """Загрузить ассет с повтором.

    Хранилище ассетов GitHub — Azure Blob, и при замене крупного файла
    (`--clobber`: сначала удаление, потом заливка) оно регулярно отвечает
    `BlobNotFound`. Сборка при этом успешна, падал только релиз, и
    артефактов не было ни на каком устройстве. Транзиент — повторяем.
    """
    last = ""
    for attempt in range(1, attempts + 1):
        result = subprocess.run(
            ["gh", "release", "upload", tag, asset, "--clobber", "--repo", repo],
            text=True,
            capture_output=True,
        )
        if result.returncode == 0:
            return
        last = (result.stderr or result.stdout or "").strip()
        print(f"  upload {Path(asset).name}: попытка {attempt}/{attempts} — {last}")
        if attempt < attempts:
            time.sleep(5 * attempt)
    sys.exit(f"не удалось загрузить {asset} после {attempts} попыток:\n{last}")


def prune_stale_assets(tag: str, assets: list[str], repo: str) -> None:
    """Убрать ассеты прежних имён из rolling-релиза.

    После ребрендинга («Карьера РТУ МИРЭА» → «Поиск МИРЭА») файлы
    называются poisk-mirea-*.apk/.ipa вместо kariera-mirea-*. Без чистки
    в релизе `preview` лежали бы ОБА набора: пользователь качал бы
    устаревшую сборку под старым именем.

    Удаляем только те типы, что пришли в этой публикации (.apk/.aab/.ipa),
    и только не совпадающие с новыми именами — прочие файлы релиза
    (например, примечания) не трогаем. Сбой чистки не валит публикацию.
    """
    import json

    fresh = {Path(a).name for a in assets}
    exts = {Path(a).suffix for a in assets}
    listing = run(
        ["gh", "release", "view", tag, "--repo", repo, "--json", "assets"],
        check=False,
    )
    if not listing:
        return
    try:
        names = [a["name"] for a in json.loads(listing).get("assets", [])]
    except (ValueError, KeyError, TypeError):
        return
    stale = [n for n in names if n not in fresh and Path(n).suffix in exts]
    for name in stale:
        run(
            ["gh", "release", "delete-asset", tag, name, "--repo", repo, "--yes"],
            check=False,
        )
        print(f"  удалён устаревший ассет: {name}")


def release_notes(version: str, prev_tag: str | None) -> str:
    """Список изменений: из CHANGELOG.md, иначе из коммитов."""
    changelog = Path("CHANGELOG.md")
    if changelog.exists():
        content = changelog.read_text(encoding="utf-8")
        marker = f"## [{version}]"
        if marker in content:
            body = content.split(marker, 1)[1]
            body = body.split("\n## [", 1)[0]
            return f"## {version}\n{body.strip()}"

    if prev_tag:
        commits = run(
            ["git", "log", f"{prev_tag}..HEAD", "--pretty=format:- %s (%h)"], check=False
        )
        if commits:
            return f"Изменения с {prev_tag}:\n\n{commits}"

    return f"Сборка {version}"


def main() -> None:
    p = argparse.ArgumentParser(description="Публикация релиза «Поиск МИРЭА»")
    p.add_argument("--channel", required=True, choices=["stable", "beta", "rc", "dev"])
    p.add_argument("--version", required=True)
    p.add_argument("--build-number", required=True)
    p.add_argument("--commit-sha", default="")
    p.add_argument("--apk", default=None)
    p.add_argument("--aab", default=None)
    p.add_argument("--ipa", default=None)
    p.add_argument("--prev-tag", default=None)
    p.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    args = p.parse_args()

    assets = [a for a in (args.apk, args.aab, args.ipa) if a and Path(a).exists()]
    if not assets:
        sys.exit("no artifacts found to publish")

    # dev-сборки живут в одном rolling-релизе `preview`:
    # иначе каждый push в main плодил бы релизы
    if args.channel == "dev":
        tag = "preview"
        title = f"Preview {args.version}"
        notes = f"Rolling dev build {args.version} (build {args.build_number})"
        prerelease = True
    else:
        tag = f"v{args.version}"
        title = f"Поиск МИРЭА {args.version}"
        notes = release_notes(args.version, args.prev_tag)
        prerelease = args.channel != "stable"

    notes += (
        f"\n\n---\n"
        f"**Сборка:** `{args.build_number}`"
        + (f"  \n**Коммит:** `{args.commit_sha}`" if args.commit_sha else "")
        + f"\n**Канал:** `{args.channel}`\n"
    )

    # существует ли релиз с этим тегом
    exists = run(
        ["gh", "release", "view", tag, "--repo", args.repo, "--json", "tagName"],
        check=False,
    )

    if exists:
        print(f"release {tag} exists - uploading assets (replacing)")
        for asset in assets:
            # --clobber: rolling-preview перезаписывает старые файлы,
            # иначе gh откажется загружать одноимённый ассет
            upload_asset(tag, asset, args.repo)
        prune_stale_assets(tag, assets, args.repo)
        gh(
            "release", "edit", tag,
            "--title", title, "--notes", notes, "--repo", args.repo,
            "--prerelease" if prerelease else "--latest",
        )
    else:
        print(f"creating release {tag}")
        flags = ["--prerelease"] if prerelease else ["--latest"]
        if args.channel == "dev":
            flags = ["--prerelease"]
        gh(
            "release", "create", tag,
            "--title", title, "--notes", notes, "--repo", args.repo,
            *flags,
            *assets,
        )

    url = gh("release", "view", tag, "--repo", args.repo, "--json", "url",
             "--jq", ".url", check=False)
    print(f"\nRelease: {url or tag}")
    print(f"Assets : {', '.join(Path(a).name for a in assets)}")

    if args.ipa and Path(args.ipa).exists():
        update_gbox_source(args, tag)


# ---------------- Источник GBox/AltStore ----------------

APP_NAME = "Поиск МИРЭА"
BUNDLE_ID = "ru.mirea.poisk"
DEVELOPER = "Vibe Moments Technologies"
TINT = "0A84FF"  # AppColors.accent
DESCRIPTION = (
    "Вакансии, стажировки, стипендии и события РТУ МИРЭА и партнёров "
    "в одной ленте. Без регистрации: избранное и профиль хранятся "
    "только на устройстве."
)


def update_gbox_source(args, tag: str) -> None:
    """Обновить apps.json на gh-pages — источник для GBox/AltStore/SideStore.

    Без этого файла магазин-клиент не видит новых сборок: он читает
    downloadURL и version из JSON, а не страницу релиза. Раньше источник
    указывал на старый репозиторий и старое имя IPA — после ребрендинга
    обе ссылки отдавали 404, и установка не работала.

    Пишем через Contents API (без клонирования gh-pages): один файл,
    атомарно, с sha предыдущей версии. Сбой не валит публикацию релиза —
    артефакты уже загружены, источник можно обновить повторным запуском.
    """
    import base64
    import json
    from datetime import datetime, timezone

    repo = args.repo
    path = "apps.json"
    branch = "gh-pages"
    now = datetime.now(timezone.utc)
    ipa_name = Path(args.ipa).name
    download = f"https://github.com/{repo}/releases/download/{tag}/{ipa_name}"
    icon = (
        f"https://raw.githubusercontent.com/{repo}/main/app/ios/Runner/"
        "Assets.xcassets/AppIcon.appiconset/Icon-App-1024x1024@1x.png"
    )
    entry = {
        "name": APP_NAME,
        "bundleIdentifier": BUNDLE_ID,
        "developerName": DEVELOPER,
        "localizedDescription": (
            f"{DESCRIPTION}\n\nКанал: {args.channel}\n"
            f"Сборка: {args.version}\n"
            f"Обновлено: {now.strftime('%Y-%m-%d %H:%M')} UTC"
        ),
        "iconURL": icon,
        "version": args.version,
        "versionDate": now.isoformat(),
        "downloadURL": download,
        "tintColor": TINT,
        "channel": args.channel,
        "size": Path(args.ipa).stat().st_size,
    }
    source = {
        "name": APP_NAME,
        "identifier": "poisk-mirea",
        "sourceURL": f"https://raw.githubusercontent.com/{repo}/{branch}/{path}",
        "apps": [entry],
    }

    api = f"repos/{repo}/contents/{path}?ref={branch}"
    sha = None
    existing = run(["gh", "api", api, "--jq", ".sha"], check=False)
    if existing and len(existing) == 40:
        sha = existing

    payload = {
        "message": f"source: {APP_NAME} {args.version} ({args.channel})",
        "content": base64.b64encode(
            json.dumps(source, ensure_ascii=False, indent=2).encode("utf-8")
        ).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    tmp = Path(".gbox-source-payload.json")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    try:
        result = subprocess.run(
            ["gh", "api", "-X", "PUT", f"repos/{repo}/contents/{path}",
             "--input", str(tmp)],
            text=True, capture_output=True,
        )
        if result.returncode == 0:
            print(f"GBox source: {source['sourceURL']}")
        else:
            print(f"::warning::источник GBox не обновлён: {result.stderr.strip()}")
    finally:
        tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()