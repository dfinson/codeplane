"""Pressure test for multilang import extraction."""
from __future__ import annotations
import sys
from pathlib import Path

# Add src to path
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

from codeplane.index import Database, IndexCoordinator

CACHE_DIR = Path(__file__).parent

def index_repo(repo_path: Path, language: str) -> Database:
    """Index a repo and return the database."""
    codeplane_dir = repo_path / ".codeplane"
    codeplane_dir.mkdir(parents=True, exist_ok=True)
    
    db_path = codeplane_dir / "index.db"
    tantivy_path = codeplane_dir / "tantivy"
    
    coordinator = IndexCoordinator(repo_path, db_path, tantivy_path)
    coordinator.full_index(root=repo_path, language=language, context_id=f"test_{repo_path.name}")
    return coordinator.db

def query_import_stats(db: Database) -> dict[str, int]:
    """Get import counts by kind."""
    with db.session_scope() as session:
        conn = session.connection().connection
        cursor = conn.cursor()
        cursor.execute("SELECT import_kind, COUNT(*) FROM import_facts GROUP BY import_kind ORDER BY COUNT(*) DESC")
        return {row[0]: row[1] for row in cursor.fetchall()}

def test_repo(name: str, language: str) -> tuple[bool, dict]:
    repo_path = CACHE_DIR / name
    if not repo_path.exists():
        return False, {"error": f"Not found: {repo_path}"}
    
    print(f"\n{'='*60}")
    print(f"Testing {language.upper()}: {name}")
    print(f"{'='*60}")
    
    db = index_repo(repo_path, language)
    stats = query_import_stats(db)
    
    print(f"  Import stats: {stats}")
    
    # Validate expected import kinds exist
    expected_kinds = {
        "go": ["go_import"],
        "rust": ["rust_use"],
        "java": ["java_import"],
        "ruby": ["ruby_require", "ruby_require_relative"],
        "php": ["php_use"],
        "cpp": ["c_include"],
    }
    
    found = []
    missing = []
    for kind in expected_kinds.get(language, []):
        if stats.get(kind, 0) > 0:
            found.append(f"{kind}={stats[kind]}")
        else:
            missing.append(kind)
    
    if found:
        print(f"  ✓ Found: {', '.join(found)}")
    if missing:
        print(f"  ✗ Missing: {', '.join(missing)}")
        return False, {"stats": stats, "missing": missing}
    
    return True, {"stats": stats}

def main():
    print("\n" + "="*70)
    print("  MULTILANG IMPORT EXTRACTION PRESSURE TEST")
    print("="*70)
    
    tests = [
        ("bubbles", "go"),
        ("serde_json", "rust"),
        ("gson", "java"),
        ("rack", "ruby"),
        ("php_log", "php"),
        ("nlohmann_json", "cpp"),
    ]
    
    results = []
    for name, lang in tests:
        try:
            ok, data = test_repo(name, lang)
            results.append((name, lang, ok, data))
        except Exception as e:
            import traceback
            traceback.print_exc()
            results.append((name, lang, False, {"error": str(e)}))
    
    print("\n" + "="*70)
    print("  SUMMARY")
    print("="*70)
    
    all_ok = True
    for name, lang, ok, data in results:
        status = "✅" if ok else "❌"
        stats = data.get("stats", {})
        total = sum(stats.values()) if stats else 0
        print(f"  {status} {lang.upper():6} {name:20} imports={total:5}")
        if not ok:
            all_ok = False
            if "error" in data:
                print(f"       Error: {data['error']}")
            if "missing" in data:
                print(f"       Missing: {data['missing']}")
    
    if all_ok:
        print("\n  ✅ ALL TESTS PASSED")
        sys.exit(0)
    else:
        print("\n  ❌ SOME TESTS FAILED")
        sys.exit(1)

if __name__ == "__main__":
    main()
