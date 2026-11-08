"""Use GitHub's asset API from a build-local copy of CPMUtil, keeping upstream intact."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.parse
import urllib.request


def resolve_asset(url, cache):
    match = re.fullmatch(r"https://github\.com/([^/]+/[^/]+)/releases/download/([^/]+)/([^/?]+)", url)
    if not match:
        return url
    repo, tag, name = map(urllib.parse.unquote, match.groups())
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    metadata = cache / (hashlib.sha256((repo + '/' + tag).encode()).hexdigest() + '.json')
    if metadata.exists():
        release = json.loads(metadata.read_text(encoding='utf-8'))
    else:
        endpoint = 'https://api.github.com/repos/' + repo + '/releases/tags/' + urllib.parse.quote(tag, safe='')
        headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'Lichii-Chinese-CI'}
        if os.environ.get('GH_TOKEN'):
            headers['Authorization'] = 'Bearer ' + os.environ['GH_TOKEN']
        with urllib.request.urlopen(urllib.request.Request(endpoint, headers=headers), timeout=60) as response:
            release = json.load(response)
        metadata.write_text(json.dumps(release), encoding='utf-8')
    asset = next((a for a in release['assets'] if a['name'] == name), None)
    if not asset:
        raise RuntimeError('Missing upstream asset: ' + repo + '/' + tag + '/' + name)
    endpoint = asset['url']
    if not endpoint.startswith('https://api.github.com/repos/' + repo + '/releases/assets/'):
        raise RuntimeError('Unexpected GitHub asset endpoint')
    return endpoint


def prepare(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    original = (source / 'CMakeModules/CPMUtil.cmake').read_text(encoding='utf-8')
    adapted, count = re.subn(r'file\(DOWNLOAD\b', 'github_asset_download(', original)
    if count == 0:
        raise RuntimeError('No CPM download calls found; review upstream dependency handling')
    wrapper = r'''
function(github_asset_download url target_file)
    execute_process(
        COMMAND "@PYTHON@" "@SCRIPT@" --resolve "${url}" "@CACHE@"
        RESULT_VARIABLE result OUTPUT_VARIABLE request_url
        OUTPUT_STRIP_TRAILING_WHITESPACE)
    if(NOT result EQUAL 0)
        message(FATAL_ERROR "Could not resolve dependency asset: ${url}")
    endif()
    set(headers)
    if(request_url MATCHES "^https://api[.]github[.]com/repos/")
        list(APPEND headers HTTPHEADER "Accept: application/octet-stream")
        if(DEFINED ENV{GH_TOKEN})
            list(APPEND headers HTTPHEADER "Authorization: Bearer $ENV{GH_TOKEN}")
        endif()
        message(STATUS "Downloading dependency through GitHub asset API: ${request_url}")
    endif()
    file(DOWNLOAD "${request_url}" "${target_file}" ${ARGN} ${headers})
    cmake_parse_arguments(PARSE_ARGV 2 download "" "STATUS;LOG" "")
    foreach(output STATUS LOG)
        if(DEFINED download_${output})
            set(${download_${output}} "${${download_${output}}}" PARENT_SCOPE)
        endif()
    endforeach()
endfunction()
'''
    substitutions = {'@PYTHON@': Path(sys.executable).as_posix(),
                     '@SCRIPT@': Path(__file__).resolve().as_posix(),
                     '@CACHE@': (destination / 'metadata').as_posix()}
    for key, value in substitutions.items():
        wrapper = wrapper.replace(key, value)
    (destination / 'CPMUtil.cmake').write_text(wrapper + adapted, encoding='utf-8')
    init = destination / 'init.cmake'
    init.write_text('list(PREPEND CMAKE_MODULE_PATH "' + destination.as_posix() + '")\n', encoding='utf-8')
    if os.environ.get('GITHUB_ENV'):
        with open(os.environ['GITHUB_ENV'], 'a', encoding='utf-8') as env:
            env.write('DEPENDENCY_API_INIT=' + init.as_posix() + '\n')
    print('Prepared build-local asset API transport; upstream CPMUtil unchanged')


if __name__ == '__main__':
    if sys.argv[1] == '--resolve':
        print(resolve_asset(sys.argv[2], sys.argv[3]))
    else:
        prepare(sys.argv[1], sys.argv[2])
