"""Lossless KiCad S-expression reader: keep spans; edit only requested tokens."""
import json
import re
from dataclasses import dataclass

TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|[()]|[^\s()"]+')

@dataclass(slots=True)
class Atom:
    start: int
    end: int
    raw: str
    @property
    def value(self):
        if self.raw.startswith('"'):
            return re.sub(r'\\([\\"nrt])', lambda m: {'n':'\n','r':'\r','t':'\t'}.get(m[1],m[1]), self.raw[1:-1])
        return self.raw

@dataclass(slots=True)
class Node:
    start: int
    end: int
    items: list
    @property
    def kind(self):
        return self.items[0].value if self.items and isinstance(self.items[0],Atom) else ''
    def children(self, kind=None):
        return [x for x in self.items if isinstance(x,Node) and (kind is None or x.kind==kind)]
    def child(self, kind):
        return next(iter(self.children(kind)),None)
    def value(self, index=1, default=''):
        return self.items[index].value if len(self.items)>index and isinstance(self.items[index],Atom) else default
    def prop(self, name):
        for x in self.children('property'):
            if x.value()==name: return x
        return None

def parse(text):
    stack=[]; root=None; last=0
    for m in TOKEN.finditer(text):
        if text[last:m.start()].strip(): raise ValueError('Malformed token near '+str(last))
        last=m.end()
        t=m[0]
        if t=='(':
            n=Node(m.start(),0,[])
            if stack: stack[-1].items.append(n)
            elif root is not None: raise ValueError('Multiple roots')
            else: root=n
            stack.append(n)
        elif t==')':
            if not stack: raise ValueError('Unbalanced close')
            stack.pop().end=m.end()
        else:
            if not stack: raise ValueError('Atom outside root')
            stack[-1].items.append(Atom(m.start(),m.end(),t))
    if stack or root is None or text[last:].strip(): raise ValueError('Truncated S-expression')
    return root

def quote(text):
    return json.dumps(str(text),ensure_ascii=False)

def edits(text, replacements):
    previous=0; chunks=[]
    for start,end,value in sorted(replacements):
        if start<previous or start>end or end>len(text): raise ValueError('Overlapping or invalid edits')
        chunks.extend((text[previous:start],value))
        previous=end
    chunks.append(text[previous:])
    return ''.join(chunks)

def replace_atom(atom, value):
    return atom.start,atom.end,quote(value)

def semantic(node, omit=()):
    if isinstance(node,Atom): return node.value
    return [semantic(x,omit) for x in node.items if not isinstance(x,Node) or x.kind not in omit]
