import tempfile
from pathlib import Path

from scripts.check_image_optimizer_policy import policy_violations


ROOT = Path(__file__).resolve().parents[1]


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_repository_image_usage_is_reviewed_and_unoptimized() -> None:
    assert policy_violations(ROOT / "Website") == []


def test_new_next_image_import_requires_security_review() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(
            root,
            "components/profile-avatar.tsx",
            'import Image from "next/image"; export const A = () => <Image unoptimized />;',
        )
        _write(
            root,
            "app/new/page.tsx",
            'import Image from "next/image"; export const B = () => <Image unoptimized />;',
        )
        assert any("not security-reviewed" in item for item in policy_violations(root))


def test_reviewed_image_must_keep_unoptimized_prop() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(
            root,
            "components/profile-avatar.tsx",
            'import Image from "next/image"; export const A = () => <Image src="/a.png" />;',
        )
        assert any("must keep the unoptimized prop" in item for item in policy_violations(root))


def test_direct_optimizer_endpoint_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(
            root,
            "components/profile-avatar.tsx",
            'import Image from "next/image"; export const A = () => <Image unoptimized />;',
        )
        _write(root, "lib/image.ts", 'export const endpoint = "/_next/image";')
        assert any("optimizer endpoint" in item for item in policy_violations(root))
