"""Simple import extraction test using TreeSitterParser directly."""
from __future__ import annotations
import sys
from pathlib import Path

src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

from codeplane.index._internal.parsing import TreeSitterParser

CACHE_DIR = Path(__file__).parent

def test_repo(name: str, language: str, extension: str) -> dict:
    repo_path = CACHE_DIR / name
    if not repo_path.exists():
        return {"error": f"Not found: {repo_path}"}
    
    print(f"\n{'='*60}")
    print(f"Testing {language.upper()}: {name}")
    print(f"{'='*60}")
    
    parser = TreeSitterParser()
    
    # Find all source files
    files = list(repo_path.rglob(f"*{extension}"))[:20]  # Limit to 20 files
    print(f"  Found {len(files)} {extension} files (testing up to 20)")
    
    all_imports = []
    for f in files:
        try:
            content = f.read_text()
            result = parser.parse(f, content.encode())
            imports = parser.extract_imports(result, str(f))
            all_imports.extend(imports)
        except Exception as e:
            print(f"  ✗ Error parsing {f.name}: {e}")
    
    # Group by kind
    by_kind = {}
    for imp in all_imports:
        kind = imp.import_kind
        by_kind[kind] = by_kind.get(kind, 0) + 1
    
    print(f"  Import stats: {by_kind}")
    print(f"  Total imports: {len(all_imports)}")
    
    if all_imports:
        print(f"  Sample: {all_imports[0].source_literal}")
    
    return by_kind

def main():
    print("\n" + "="*70)
    print("  SIMPLE IMPORT EXTRACTION TEST")
    print("="*70)
    
    tests = [
        ("bubbles", "go", ".go"),
        ("serde_json", "rust", ".rs"),
        ("gson", "java", ".java"),
        ("rack", "ruby", ".rb"),
        ("php_log", "php", ".php"),
    ]
    
    for name, lang, ext in tests:
        try:
            test_repo(name, lang, ext)
        except Exception as e:
            import traceback
            traceback.print_exc()
    
    print("\n  Done!")

if __name__ == "__main__":
    main()

# Test C++ separately
def test_cpp():
    from codeplane.index._internal.parsing import TreeSitterParser
    
    print(f"\n{'='*60}")
    print(f"Testing CPP: nlohmann_json")
    print(f"{'='*60}")
    
    repo_path = CACHE_DIR / "nlohmann_json"
    parser = TreeSitterParser()
    
    # Only test small files
    files = [f for f in repo_path.rglob("*.hpp") if f.stat().st_size < 50000][:10]
    print(f"  Testing {len(files)} small .hpp files")
    
    all_imports = []
    for f in files:
        try:
            content = f.read_text()
            result = parser.parse(f, content.encode())
            imports = parser.extract_imports(result, str(f))
            all_imports.extend(imports)
        except Exception as e:
            print(f"  ✗ Error: {f.name}: {e}")
    
    by_kind = {}
    for imp in all_imports:
        by_kind[imp.import_kind] = by_kind.get(imp.import_kind, 0) + 1
    
    print(f"  Import stats: {by_kind}")
    print(f"  Total includes: {len(all_imports)}")
    if all_imports:
        print(f"  Sample: {all_imports[0].source_literal}")

if __name__ == "__main__":
    test_cpp()
