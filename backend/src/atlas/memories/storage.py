"""Pure catalog-to-directory topology, independent of memory loading rules."""
import os
from pathlib import Path, PurePosixPath


def build_memory_storage(files, home=None, selected_paths=None):
    """Group exact catalog paths without opening files or discovering new paths.

    Nodes retain intermediate directories, direct files, and subtree membership.
    Roots omit shared machine/home prefixes and leading single-child chains.
    Invalid paths remain in unplacedFiles rather than being repaired or dropped.
    Filtering preserves the full catalog's root anchors, not excluded files.
    """
    home = PurePosixPath(str(home if home is not None else Path.home()))
    nodes, parents, seen, unplaced = {}, {}, set(), []
    for entry in files:
        if entry.get('category') != 'memory':
            continue
        source = entry.get('path')
        if source in seen:
            continue
        seen.add(source)
        if not isinstance(source, str) or not source or '\x00' in source:
            unplaced.append(source)
            continue
        path = PurePosixPath(source)
        if not path.is_absolute() or '..' in path.parts or str(path) != source or path == path.parent:
            unplaced.append(source)
            continue
        parent = str(path.parent)
        parents[source] = parent
        chain = [path.parent] + list(path.parent.parents)
        for directory in chain:
            key = str(directory)
            node = nodes.setdefault(key, {'path': key, 'name': directory.name or '/',
                                         'parent': str(directory.parent), 'children': set(),
                                         'directFiles': set(), 'filePaths': set()})
            node['filePaths'].add(source)
            if key == parent:
                node['directFiles'].add(source)
            if directory != directory.parent:
                above = str(directory.parent)
                nodes.setdefault(above, {'path': above, 'name': directory.parent.name or '/',
                                        'parent': str(directory.parent.parent), 'children': set(),
                                        'directFiles': set(), 'filePaths': set()})['children'].add(key)
    roots = []
    if parents:
        common = os.path.commonpath(list(parents.values()))
        node = nodes[common]
        if (not node['directFiles'] and (PurePosixPath(common) == home or PurePosixPath(common) in home.parents)):
            roots = sorted(node['children'])
        else:
            roots = [common]
        for index, root in enumerate(roots):
            while not nodes[root]['directFiles'] and len(nodes[root]['children']) == 1:
                root = next(iter(nodes[root]['children']))
            roots[index] = root
    if selected_paths is not None:
        visible = set(selected_paths)
        seen.intersection_update(visible)
        unplaced = [path for path in unplaced if path in visible]
        for node in nodes.values():
            node['directFiles'].intersection_update(visible)
            node['filePaths'].intersection_update(visible)
        for node in nodes.values():
            node['children'] = {child for child in node['children'] if nodes[child]['filePaths']}
        roots = [root for root in roots if nodes[root]['filePaths']]
    selected, locations = {}, {}
    for root in sorted(roots):
        pending = [(root, [])]
        while pending:
            key, ancestors = pending.pop()
            original = nodes[key]
            chain = ancestors + [key]
            path = PurePosixPath(key)
            try:
                relative = path.relative_to(home)
                display = '~' + ('/' + str(relative) if relative.parts else '')
            except ValueError:
                display = key
            node = dict(original, parent=ancestors[-1] if ancestors else None,
                        children=sorted(original['children']), directFiles=sorted(original['directFiles']),
                        filePaths=sorted(original['filePaths']), displayPath=display,
                        directCount=len(original['directFiles']), totalCount=len(original['filePaths']),
                        ancestors=chain)
            selected[key] = node
            for source in node['directFiles']:
                locations[source] = {'directory': key, 'root': root, 'ancestors': chain,
                                     'relativePath': str(PurePosixPath(source).relative_to(root))}
            pending.extend((child, chain) for child in reversed(node['children']))
    return {'schemaVersion': 1, 'roots': sorted(roots), 'nodes': selected,
            'locations': locations, 'totalFiles': len(seen), 'unplacedFiles': unplaced}
