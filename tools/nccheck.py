#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""nccheck.py - NCプログラムのチェック支援ツール（FANUC 0i-MF / SOLIDWORKS CAM 出力向け）

目的は「プログラムチェックを層単位で終わらせる」こと。
層ごとのツールパスがZ以外完全に一致していることを機械的に確認できれば、
1層分を目視するだけで全層のチェックが済む。

使い方:
    python nccheck.py ops     FILE            工程一覧を表示
    python nccheck.py layers  FILE            層ごとのdiff判定（本命）
    python nccheck.py check   FILE            安全チェック（G54欠落・G52残留など）
    python nccheck.py extract FILE --ops 2,3  指定工程だけ抜き出して標準出力へ
    python nccheck.py all     FILE            ops + layers + check をまとめて

オプション:
    --tol 0.0005   座標の一致判定に使う許容差[mm]（既定 0.0005）
    --show N       不一致の層について先頭N行のdiffを表示（既定 6、0で非表示）
"""

import argparse
import difflib
import io
import re
import sys
from collections import OrderedDict

WORD_RE = re.compile(r'([A-Za-z])\s*([+-]?(?:\d+\.?\d*|\.\d+))')
COMMENT_RE = re.compile(r'\([^)]*\)')

MOTION_CODES = {0, 1, 2, 3}
CANNED_CODES = {73, 74, 76, 81, 82, 83, 84, 85, 86, 87, 88, 89}


# ---------------------------------------------------------------- パース

class Block(object):
    """NCの1行。"""

    __slots__ = ('n', 'raw', 'text', 'comment', 'skip', 'words', 'gcodes',
                 'mcodes', 'x', 'y', 'z', 'motion', 'absinc', 'zpos', 'canned')

    def __init__(self, lineno, raw):
        self.n = lineno
        self.raw = raw.rstrip('\r\n')
        body = self.raw.strip()
        self.comment = ' '.join(COMMENT_RE.findall(body))
        body = COMMENT_RE.sub(' ', body)
        self.skip = body.startswith('/')
        if self.skip:
            body = body[1:]
        self.text = body.strip()
        self.words = WORD_RE.findall(self.text)
        self.gcodes = []
        self.mcodes = []
        self.x = self.y = self.z = None
        for letter, value in self.words:
            up = letter.upper()
            if up == 'G':
                self.gcodes.append(int(round(float(value))))
            elif up == 'M':
                self.mcodes.append(int(round(float(value))))
            elif up == 'X':
                self.x = float(value)
            elif up == 'Y':
                self.y = float(value)
            elif up == 'Z':
                self.z = float(value)
        self.motion = None
        self.absinc = 90
        self.zpos = None
        self.canned = False

    def has(self, letter):
        letter = letter.upper()
        return any(w[0].upper() == letter for w in self.words)

    def value(self, letter):
        letter = letter.upper()
        for lt, v in self.words:
            if lt.upper() == letter:
                return float(v)
        return None

    @property
    def is_cut(self):
        """切削送りでXYが動くブロックか。"""
        return self.motion in (1, 2, 3) and (self.x is not None or self.y is not None)


def parse(path):
    with io.open(path, 'r', encoding='utf-8', errors='replace') as fh:
        lines = fh.readlines()

    blocks = []
    motion = None
    absinc = 90
    z = None
    canned = False
    for i, raw in enumerate(lines, 1):
        b = Block(i, raw)
        for g in b.gcodes:
            if g in MOTION_CODES:
                motion = g
                canned = False
            elif g in CANNED_CODES:
                canned = True
                motion = None
            elif g == 80:
                canned = False
                motion = None
            elif g in (90, 91):
                absinc = g
        b.absinc = absinc
        b.canned = canned
        b.motion = motion
        if b.z is not None:
            z = b.z if absinc == 90 else ((z or 0.0) + b.z)
        b.zpos = z
        blocks.append(b)
    return blocks


# ---------------------------------------------------------------- 工程分割

class Operation(object):
    def __init__(self, index, start):
        self.index = index
        self.start = start
        self.end = None
        self.tool = None
        self.hcode = None
        self.dcodes = []
        self.wcs = []
        self.comment = ''
        self.spindle = None


def _back_up_boundary(blocks, i):
    """M06の手前にある工具コメントとM05を、その工程の先頭に含める。"""
    j = i
    for _ in range(4):
        k = j - 1
        if k < 0:
            break
        prev = blocks[k]
        comment_only = prev.comment and not prev.words
        m05_only = prev.mcodes == [5] and not prev.gcodes
        if comment_only or m05_only:
            j = k
        else:
            break
    return j


def split_ops(blocks):
    """工具交換（M06）を境界に工程へ分割する。M06より前はヘッダ扱い。"""
    bounds = [_back_up_boundary(blocks, i)
              for i, b in enumerate(blocks) if 6 in b.mcodes]
    ops = []
    if not bounds:
        op = Operation(1, 0)
        op.end = len(blocks) - 1
        ops.append(op)
    else:
        for k, s in enumerate(bounds):
            op = Operation(k + 1, s)
            op.end = (bounds[k + 1] - 1) if k + 1 < len(bounds) else len(blocks) - 1
            ops.append(op)

    for op in ops:
        for b in blocks[op.start:op.end + 1]:
            if op.tool is None and b.has('T'):
                op.tool = int(b.value('T'))
            if 43 in b.gcodes and b.has('H'):
                op.hcode = int(b.value('H'))
            if (41 in b.gcodes or 42 in b.gcodes) and b.has('D'):
                d = int(b.value('D'))
                if d not in op.dcodes:
                    op.dcodes.append(d)
            for g in b.gcodes:
                if 54 <= g <= 59 and g not in op.wcs:
                    op.wcs.append(g)
            if b.has('S') and op.spindle is None:
                op.spindle = b.value('S')
            if not op.comment and b.comment:
                op.comment = b.comment
    return ops


# ---------------------------------------------------------------- 層分割

def find_layers(blocks, op, tol):
    """工程内を層に分ける。

    層 = 「Zが一定のまま切削するまとまり」1つ分。
    侵入（ヘリカル／ランプ）は、その侵入が到達する層に含める。
    """
    idx = list(range(op.start, op.end + 1))

    levels = []          # [z, 平坦切削が最後に現れた行index]
    prev_z = None
    for i in idx:
        b = blocks[i]
        if b.canned:
            if b.zpos is not None:
                prev_z = b.zpos
            continue
        if b.is_cut and b.zpos is not None:
            if prev_z is not None and abs(b.zpos - prev_z) <= tol:
                if levels and abs(levels[-1][0] - b.zpos) <= tol:
                    levels[-1][1] = i
                else:
                    levels.append([b.zpos, i])
        if b.zpos is not None:
            prev_z = b.zpos

    if len(levels) < 2:
        return []

    layers = []
    cursor = idx[0]
    for z, last in levels:
        # 層の始まりは最初の切削送り。工具交換・早送り位置決めは層に含めない
        start = cursor
        while start < last and blocks[start].motion not in (1, 2, 3):
            start += 1
        layers.append({'z': z, 'start': start, 'end': last})
        cursor = last + 1
    return layers


def normalize(blocks, layer):
    """層を、Zを層基準面からの相対値に直した正規形テキストにする。"""
    base = layer['z']
    out = []
    for i in range(layer['start'], layer['end'] + 1):
        b = blocks[i]
        if not b.text:
            continue
        parts = []
        for letter, value in b.words:
            up = letter.upper()
            if up == 'N':
                continue          # シーケンス番号は必ず違うので除外
            if up == 'Z' and b.absinc == 90:
                parts.append('Z%+.4f' % (float(value) - base))
            else:
                parts.append('%s%s' % (up, value))
        if parts:
            out.append(' '.join(parts))
    return out


def analyze_layers(blocks, ops, tol):
    results = []
    for op in ops:
        layers = find_layers(blocks, op, tol)
        entry = {'op': op, 'layers': layers, 'groups': OrderedDict(), 'norms': []}
        for ly in layers:
            norm = normalize(blocks, ly)
            entry['norms'].append(norm)
            entry['groups'].setdefault('\n'.join(norm), []).append(len(entry['norms']))
        results.append(entry)
    return results


# ---------------------------------------------------------------- 出力

def fmt_ops(blocks, ops):
    print('=== 工程一覧 ===')
    for op in ops:
        zs = [b.zpos for b in blocks[op.start:op.end + 1] if b.zpos is not None]
        zr = ('Z %8.3f 〜 %8.3f' % (min(zs), max(zs))) if zs else 'Z なし'
        print('  工程%-2d  行%5d-%-5d  T%-3s H%-3s D%-6s %-8s %s'
              % (op.index, blocks[op.start].n, blocks[op.end].n,
                 op.tool if op.tool is not None else '-',
                 op.hcode if op.hcode is not None else '-',
                 ','.join(str(d) for d in op.dcodes) or '-',
                 ','.join('G%d' % g for g in op.wcs) or '★なし',
                 zr))
        if op.comment:
            print('          %s' % op.comment)


def diff_lines(a, b, limit):
    out = []
    for line in difflib.unified_diff(a, b, lineterm='', n=0):
        if line.startswith('---') or line.startswith('+++'):
            continue
        out.append(line)
        if len(out) >= limit:
            out.append('... (以下省略)')
            break
    return out


def fmt_layers(blocks, results, show):
    print('=== 層チェック ===')
    found = False
    for entry in results:
        op, layers, groups = entry['op'], entry['layers'], entry['groups']
        head = '  工程%d (T%s)' % (op.index, op.tool if op.tool is not None else '-')
        if not layers:
            print('%s  層構造なし（穴あけサイクル／単層）— 対象外' % head)
            continue
        found = True
        print('%s  層数 %d' % (head, len(layers)))

        zs = [ly['z'] for ly in layers]
        print('     Z: %s' % ' / '.join('%.3f' % z for z in zs))
        steps = [round(zs[i] - zs[i + 1], 4) for i in range(len(zs) - 1)]
        if steps:
            uniq = sorted(set(steps))
            if len(uniq) == 1:
                print('     切り込み: %.3f 均等 ○' % uniq[0])
            else:
                print('     切り込み: %s ← 不均等（最初/最後の層は仕様どおりか要確認）'
                      % ' / '.join('%.3f' % s for s in steps))

        biggest = max(groups.values(), key=len)
        if len(groups) == 1:
            print('     ★全%d層がZ以外完全一致 → 1層見ればチェック完了' % len(layers))
            continue

        print('     一致グループ %d 種:' % len(groups))
        for members in groups.values():
            mark = ' ←最大' if members is biggest else ''
            print('       層 %s (%d層)%s'
                  % (','.join(str(m) for m in members), len(members), mark))
        print('     → 層%d を目視し、下記の相違だけ追加で確認すれば済む' % biggest[0])
        if show:
            ref = entry['norms'][biggest[0] - 1]
            for members in groups.values():
                if members is biggest:
                    continue
                print('     --- 層%d vs 層%d ---' % (biggest[0], members[0]))
                for line in diff_lines(ref, entry['norms'][members[0] - 1], show):
                    print('       %s' % line)
    if not found:
        print('  層構造のある工程がありませんでした。')


def fmt_check(blocks, ops):
    print('=== 安全チェック ===')
    warn = []

    if not any(54 <= g <= 59 for b in blocks for g in b.gcodes):
        warn.append('G54〜G59 が1つも出力されていない。'
                    'セットアップの「ワーク座標系オフセット」未設定の可能性が高い')
    for op in ops:
        if op.tool is None:
            continue
        if not op.wcs:
            warn.append('工程%d (T%s) にワーク座標系の指令がない' % (op.index, op.tool))
        if op.hcode is None:
            warn.append('工程%d (T%s) に G43 H__ が見当たらない' % (op.index, op.tool))
        if op.hcode == 0:
            warn.append('工程%d の工具長補正が H0（補正なし）になっている' % op.index)
        if 0 in op.dcodes:
            warn.append('工程%d の径補正が D0 になっている' % op.index)

    g52 = [b for b in blocks if 52 in b.gcodes]
    if g52:
        last = g52[-1]
        if any(abs(v or 0.0) > 1e-9 for v in (last.x, last.y, last.z)):
            warn.append('最後の G52 (%d行目) がゼロに戻っていない。'
                        'ローカル座標系がモーダルに残り、次のプログラムがずれる' % last.n)

    absinc = 90
    for b in blocks:
        for g in b.gcodes:
            if g in (90, 91):
                absinc = g
    if absinc == 91:
        warn.append('プログラム終了時が G91（増分）のまま')

    for i, b in enumerate(blocks):
        if 28 in b.gcodes and 91 in b.gcodes:
            nxt = blocks[i + 1] if i + 1 < len(blocks) else None
            if nxt is None or 90 not in nxt.gcodes:
                warn.append('%d行目の G91 G28 の直後に G90 がない（次の移動が増分になる）' % b.n)

    on = sum(1 for b in blocks if 41 in b.gcodes or 42 in b.gcodes)
    off = sum(1 for b in blocks if 40 in b.gcodes)
    if on and off < on:
        warn.append('径補正の立ち上げ %d回 に対しキャンセル(G40) %d回' % (on, off))

    if warn:
        for w in warn:
            print('  [!] %s' % w)
    else:
        print('  問題は見つかりませんでした。')
    return warn


def do_extract(blocks, ops, spec):
    want = set()
    for part in spec.split(','):
        part = part.strip()
        if '-' in part:
            a, b = part.split('-', 1)
            want.update(range(int(a), int(b) + 1))
        elif part:
            want.add(int(part))

    out = []
    header_end = ops[0].start - 1 if ops and ops[0].start > 0 else -1
    for i in range(0, header_end + 1):
        out.append(blocks[i].raw)
    for op in ops:
        if op.index in want:
            for i in range(op.start, op.end + 1):
                out.append(blocks[i].raw)

    sys.stderr.write('抜き出した工程: %s\n' % ','.join(str(i) for i in sorted(want)))
    sys.stderr.write('※ 末尾の M30 と、飛ばした工程のモーダル指令'
                     '（G54/G43/S/M03/G90）が欠けていないか必ず確認すること\n')
    return out


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description='NCプログラムのチェック支援')
    ap.add_argument('command', choices=['ops', 'layers', 'check', 'extract', 'all'])
    ap.add_argument('file')
    ap.add_argument('--tol', type=float, default=0.0005)
    ap.add_argument('--show', type=int, default=6)
    ap.add_argument('--ops', default='')
    args = ap.parse_args(argv)

    blocks = parse(args.file)
    ops = split_ops(blocks)

    if args.command == 'extract':
        if not args.ops:
            ap.error('extract には --ops 2,3 のように工程番号を指定してください')
        for line in do_extract(blocks, ops, args.ops):
            print(line)
        return 0

    if args.command in ('ops', 'all'):
        fmt_ops(blocks, ops)
        print('')
    if args.command in ('layers', 'all'):
        fmt_layers(blocks, analyze_layers(blocks, ops, args.tol), args.show)
        print('')
    if args.command in ('check', 'all'):
        fmt_check(blocks, ops)
    return 0


if __name__ == '__main__':
    sys.exit(main())
