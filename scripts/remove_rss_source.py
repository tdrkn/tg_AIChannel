from __future__ import annotations

import argparse
from pathlib import Path

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pattern", help="Substring to remove from NEWS_RSS_URLS (e.g. techradar)")
    args = parser.parse_args()
    
    env_path = Path(".env")
    if not env_path.exists():
        print("No .env found")
        return 1

    lines = env_path.read_text(encoding="utf-8").splitlines()
    out_lines = []
    
    modified = False
    
    for line in lines:
        if line.startswith("NEWS_RSS_URLS="):
            key, val = line.split("=", 1)
            urls = [u.strip() for u in val.split(",") if u.strip()]
            
            new_urls = []
            for u in urls:
                if args.pattern.lower() in u.lower():
                    print(f"Removing RSS URL: {u}")
                    modified = True
                else:
                    new_urls.append(u)
            
            out_lines.append(f"NEWS_RSS_URLS={','.join(new_urls)}")
        else:
            out_lines.append(line)
            
    if modified:
        env_path.with_suffix(".env.bak_techradar").write_text("\n".join(lines) + "\n", encoding="utf-8")
        env_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        print("Updated .env")
    else:
        print(f"Pattern '{args.pattern}' not found in NEWS_RSS_URLS")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
