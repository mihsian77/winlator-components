#!/usr/bin/env python3
"""
自动同步组件的通用脚本
支持从 WCPHub (zstd)、WinNative (XZ)、hostei33 (直接 tzst) 同步
"""
import os
import io
import json
import tarfile
import zstandard as zstd
import lzma
import urllib.request
import subprocess
import sys
import time

TOKEN = os.environ.get("TOKEN", "")
OUR_REPO = "mihsian77/winlator-components"
# 每夜版只保留最近几个，避免天天堆一个新包
NIGHTLY_KEEP = 3

def api_get(url, retries=3):
    # 上游偶发 502/504，一次超时不该把整轮同步打挂
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url)
            if TOKEN:
                req.add_header("Authorization", f"token {TOKEN}")
            req.add_header("X-GitHub-Api-Version", "2022-11-28")
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except Exception as e:
            last = e
            if attempt < retries - 1:
                wait = 3 * (attempt + 1)
                print(f"  api_get failed ({e}), retry in {wait}s")
                time.sleep(wait)
    raise last

def download_file(url, dest, retries=3):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url)
            if TOKEN:
                req.add_header("Authorization", f"token {TOKEN}")
            with urllib.request.urlopen(req, timeout=60) as resp:
                with open(dest, 'wb') as f:
                    f.write(resp.read())
            return
        except Exception as e:
            last = e
            if attempt < retries - 1:
                print(f"  download failed ({e}), retry in 5s")
                time.sleep(5)
    raise last

def convert_zstd_wcp_to_tzst(wcp_path, tzst_path):
    """WCPHub 的 wcp (zstd) → tzst"""
    import tempfile, shutil
    tmpdir = tempfile.mkdtemp()
    try:
        with open(wcp_path, 'rb') as f:
            dctx = zstd.ZstdDecompressor()
            reader = dctx.stream_reader(f)
            with tarfile.open(fileobj=reader, mode='r:') as tar:
                tar.extractall(tmpdir, filter='data')
        
        # 删 profile.json
        profile_path = os.path.join(tmpdir, 'profile.json')
        if os.path.exists(profile_path):
            os.remove(profile_path)
        
        # 重新打包成 tzst
        with open(tzst_path, 'wb') as f:
            cctx = zstd.ZstdCompressor(level=3)
            with cctx.stream_writer(f) as compressor:
                with tarfile.open(fileobj=compressor, mode='w') as tar:
                    for root, dirs, files in os.walk(tmpdir):
                        for file in files:
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, tmpdir)
                            tar.add(file_path, arcname=arcname)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

def convert_xz_wcp_to_tzst(wcp_path, tzst_path):
    """WinNative 的 wcp (XZ) → tzst"""
    import tempfile, shutil
    tmpdir = tempfile.mkdtemp()
    try:
        with lzma.open(wcp_path, 'rb') as f:
            with tarfile.open(fileobj=f, mode='r:') as tar:
                tar.extractall(tmpdir, filter='data')
        
        # 删 profile.json
        profile_path = os.path.join(tmpdir, 'profile.json')
        if os.path.exists(profile_path):
            os.remove(profile_path)
        
        # 重新打包成 tzst
        with open(tzst_path, 'wb') as f:
            cctx = zstd.ZstdCompressor(level=3)
            with cctx.stream_writer(f) as compressor:
                with tarfile.open(fileobj=compressor, mode='w') as tar:
                    for root, dirs, files in os.walk(tmpdir):
                        for file in files:
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, tmpdir)
                            tar.add(file_path, arcname=arcname)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

def get_existing_files(release_tag):
    """获取我们仓库某个 release 已有的文件列表"""
    try:
        data = api_get(f"https://api.github.com/repos/{OUR_REPO}/releases/tags/{release_tag}")
        return {a['name'] for a in data.get('assets', [])}
    except:
        return set()

def sync_wcphub():
    """同步 WCPHub (稳定版)"""
    print("=== Syncing WCPHub ===")
    WCPHUB_REPO = "Arihany/WinlatorWCPHub"
    
    # 要同步的 release 标签
    sync_map = {
        "DXVK": ("dxvk", "zstd"),
        "DXVK-ARM64EC": ("dxvk", "zstd"),
        "DXVK-GPLASYNC": ("dxvk", "zstd"),
        "DXVK-GPLASYNC-ARM64EC": ("dxvk", "zstd"),
        "DXVK-SAREK-ASYNC": ("dxvk", "zstd"),
        "DXVK-SAREK-ASYNC-ARM64EC": ("dxvk", "zstd"),
        "VKD3D-PROTON": ("vkd3d", "zstd"),
        "VKD3D-PROTON-ARM64EC": ("vkd3d", "zstd"),
    }
    
    downloaded = 0
    uploaded = 0
    
    for tag, (target_release, fmt) in sync_map.items():
        print(f"  Checking {tag}...")
        try:
            data = api_get(f"https://api.github.com/repos/{WCPHUB_REPO}/releases/tags/{tag}")
            existing = get_existing_files(target_release)
            
            for asset in data.get('assets', []):
                wcp_name = asset['name']
                if not wcp_name.endswith('.wcp'):
                    continue
                
                tzst_name = wcp_name.replace('.wcp', '.tzst')
                if tzst_name in existing:
                    continue  # 已经存在
                
                # 下载并转换
                wcp_path = f"/tmp/{wcp_name}"
                tzst_path = f"/tmp/{tzst_name}"
                download_file(asset['browser_download_url'], wcp_path)
                
                if fmt == "zstd":
                    convert_zstd_wcp_to_tzst(wcp_path, tzst_path)
                
                # 上传到我们的 release
                subprocess.run([
                    "gh", "release", "upload", target_release,
                    tzst_path, "--repo", OUR_REPO, "--clobber"
                ], env={**os.environ, "GH_TOKEN": TOKEN}, check=True)
                
                downloaded += 1
                uploaded += 1
                print(f"    + {tzst_name}")
                
                # 清理
                os.remove(wcp_path)
                os.remove(tzst_path)
        except Exception as e:
            print(f"    Error: {e}")
    
    print(f"  Downloaded: {downloaded}, Uploaded: {uploaded}")
    return uploaded

def sync_winnative():
    """同步 WinNative (每夜版)"""
    print("=== Syncing WinNative ===")
    WINNATIVE_REPO = "nicholasx417/WinNative-Components"
    
    # 要同步的 release 标签前缀
    sync_map = {
        "dxvk-nightly": "dxvk-nightly",
        "vkd3d-nightly": "vkd3d-nightly",
    }
    
    downloaded = 0
    uploaded = 0
    
    try:
        releases = api_get(f"https://api.github.com/repos/{WINNATIVE_REPO}/releases?per_page=100")
    except Exception as e:
        print(f"  Error fetching releases: {e}")
        return 0
    
    for release in releases:
        tag = release['tag_name']
        
        # 判断是 DXVK 还是 VKD3D
        if tag.startswith("dxvk-nightly"):
            target = "dxvk-nightly"
        elif tag.startswith("vkd3d-nightly"):
            target = "vkd3d-nightly"
        else:
            continue
        
        print(f"  Checking {tag}...")
        existing = get_existing_files(target)
        
        for asset in release.get('assets', []):
            wcp_name = asset['name']
            if not wcp_name.endswith('.wcp'):
                continue
            
            tzst_name = wcp_name.replace('.wcp', '.tzst')
            if tzst_name in existing:
                continue
            
            # 下载并转换
            wcp_path = f"/tmp/{wcp_name}"
            tzst_path = f"/tmp/{tzst_name}"
            download_file(asset['browser_download_url'], wcp_path)
            
            # WinNative 是 XZ 格式
            convert_xz_wcp_to_tzst(wcp_path, tzst_path)
            
            # 上传到我们的 release（直接用 curl + GitHub API）
            # 先获取 release ID
            release_id = json.loads(subprocess.run([
                "curl", "-s", "-H", f"Authorization: token {TOKEN}",
                f"https://api.github.com/repos/{OUR_REPO}/releases/tags/{target}"
            ], capture_output=True, text=True).stdout)['id']
            
            # 上传 asset
            upload_url = f"https://uploads.github.com/repos/{OUR_REPO}/releases/{release_id}/assets?name={tzst_name}"
            subprocess.run([
                "curl", "-s", "-X", "POST",
                "-H", f"Authorization: token {TOKEN}",
                "-H", "Content-Type: application/octet-stream",
                "--data-binary", f"@{tzst_path}",
                upload_url
            ], check=True)
            
            downloaded += 1
            uploaded += 1
            print(f"    + {tzst_name}")
            
            os.remove(wcp_path)
            os.remove(tzst_path)
    
    print(f"  Downloaded: {downloaded}, Uploaded: {uploaded}")
    return uploaded

def sync_hostei33():
    """同步 hostei33 组件站 (直接 tzst)"""
    print("=== Syncing hostei33 ===")
    BASE_URL = "http://cdn4.52emu.cn/wlt/v10/installable_components"
    
    # 要同步的组件类型
    sync_map = {
        "dxvk": "dxvk",
        "vkd3d": "vkd3d",
        "box64": "box64",
    }
    
    downloaded = 0
    uploaded = 0
    
    for component_type, target_release in sync_map.items():
        print(f"  Checking {component_type}...")
        try:
            # 获取 index.txt
            url = f"{BASE_URL}/{component_type}/index.txt"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req) as resp:
                index_content = resp.read().decode('utf-8')
            
            files = [f.strip() for f in index_content.strip().split('\n') if f.strip()]
            existing = get_existing_files(target_release)
            
            for tzst_name in files:
                if tzst_name in existing:
                    continue
                
                # 下载 tzst 文件
                tzst_url = f"{BASE_URL}/{component_type}/{tzst_name}"
                tzst_path = f"/tmp/{tzst_name}"
                download_file(tzst_url, tzst_path)
                
                # 上传到我们的 release
                subprocess.run([
                    "gh", "release", "upload", target_release,
                    tzst_path, "--repo", OUR_REPO, "--clobber"
                ], env={**os.environ, "GH_TOKEN": TOKEN}, check=True)
                
                downloaded += 1
                uploaded += 1
                print(f"    + {tzst_name}")
                
                os.remove(tzst_path)
        except Exception as e:
            print(f"    Error: {e}")
    
    print(f"  Downloaded: {downloaded}, Uploaded: {uploaded}")
    return uploaded

def prune_nightly():
    """每夜版每天一个新包，旧的不删会一直堆在列表里，只保留最近几个"""
    print("=== Pruning nightly ===")
    deleted = 0
    for target in ["vkd3d-nightly", "dxvk-nightly"]:
        try:
            data = api_get(f"https://api.github.com/repos/{OUR_REPO}/releases/tags/{target}")
        except Exception as e:
            print(f"  {target}: Error - {e}")
            continue

        assets = sorted(data.get('assets', []), key=lambda a: a['created_at'], reverse=True)
        for asset in assets[NIGHTLY_KEEP:]:
            code = subprocess.run([
                "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                "-X", "DELETE",
                "-H", f"Authorization: token {TOKEN}",
                f"https://api.github.com/repos/{OUR_REPO}/releases/assets/{asset['id']}"
            ], capture_output=True, text=True).stdout
            if code.startswith("2"):
                print(f"  - {asset['name']}")
                deleted += 1
            else:
                print(f"  ! {asset['name']} delete failed: HTTP {code}")

    print(f"  Deleted: {deleted}")
    return deleted


def update_index():
    """更新所有 index.txt"""
    print("=== Updating index.txt ===")
    
    releases = ["dxvk", "vkd3d", "box64", "turnip", "wined3d", "dxvk-nightly", "vkd3d-nightly"]
    
    for release in releases:
        try:
            data = api_get(f"https://api.github.com/repos/{OUR_REPO}/releases/tags/{release}")
            files = sorted([a['name'] for a in data.get('assets', [])])

            # 自编译产物统一带 -dev 后缀，只用于本机测试，不进公开列表。
            # build.yml 那边也会过滤一次，但每次每夜同步重写 index 会把它覆盖回来。
            files = [f for f in files if '-dev' not in f]

            # 写入 index.txt
            index_path = f"/tmp/{release}_index.txt"
            with open(index_path, 'w') as f:
                f.write('\n'.join(files) + '\n')

            # 先落盘再 add，否则 add 进去的还是上一版内容
            os.system(f"mkdir -p {release} && cp {index_path} {release}/index.txt")
            subprocess.run([
                "git", "add", f"{release}/index.txt"
            ], check=True)

            print(f"  {release}: {len(files)} files")
        except Exception as e:
            print(f"  {release}: Error - {e}")

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    
    total = 0
    
    if mode in ["all", "wcphub"]:
        total += sync_wcphub()
    
    if mode in ["all", "winnative"]:
        total += sync_winnative()
        total += prune_nightly()
    
    if mode in ["all", "hostei33"]:
        total += sync_hostei33()
    
    if total > 0:
        update_index()
        print(f"\n=== Total new files: {total} ===")
    else:
        print("\n=== No new files ===")

if __name__ == '__main__':
    main()
