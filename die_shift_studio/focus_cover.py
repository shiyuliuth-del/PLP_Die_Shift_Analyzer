"""Deterministic covering array of complete per-DIE ternary pose states."""
from itertools import product

def pair_cover(radices):
    q=max(radices,default=1)
    # Coincident 0/endpoints may collapse ternary grids to sizes other than
    # powers of three. Surjective modulo projection still covers every pair.
    degree=next(m for m in (1,2,3) if 3**m>=q);q=3**degree
    def add(a,b):
        result=0;place=1
        for _ in range(degree):
            result+=((a%3+b%3)%3)*place;a//=3;b//=3;place*=3
        return result
    def multiply(a,b):
        aa=[];bb=[]
        for _ in range(degree):aa.append(a%3);bb.append(b%3);a//=3;b//=3
        c=[0]*(2*degree-1)
        for i,x in enumerate(aa):
            for j,y in enumerate(bb):c[i+j]=(c[i+j]+x*y)%3
        # irreducible polynomials x, x²+1, x³+2x+1 over GF(3).
        poly={1:(0,),2:(1,0),3:(1,2,0)}[degree]
        for i in range(len(c)-1,degree-1,-1):
            for j,v in enumerate(poly):c[i-degree+j]=(c[i-degree+j]-c[i]*v)%3
        return sum(c[i]*3**i for i in range(degree))
    rows=set()
    for u,v in product(range(q),repeat=2):
        values=[u,v]+[add(u,multiply(a,v)) for a in range(1,q)]
        rows.add(tuple(values[i%(q+1)]%r for i,r in enumerate(radices)))
    # More than q+1 DIEs reuse columns. Repair only repeated-column pairs;
    # never silently claim coverage for those pairs.
    for i in range(len(radices)):
        for j in range(i+1,len(radices)):
            if i%(q+1)!=j%(q+1):continue
            for a,b in product(range(radices[i]),range(radices[j])):
                row=[0]*len(radices);row[i]=a;row[j]=b;rows.add(tuple(row))
    return sorted(rows)
