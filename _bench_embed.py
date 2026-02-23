"""Embedding throughput benchmark — batch_size × sort order."""
import time, os, random
os.environ['TOKENIZERS_PARALLELISM'] = 'false'

from fastembed import TextEmbedding
m = TextEmbedding('BAAI/bge-small-en-v1.5', threads=10)

random.seed(42)
texts = []
for _ in range(1500): texts.append(' '.join(random.choices(['get','set','user','config','handler','validate','process','init','auth','data'], k=random.randint(2,5))))
for _ in range(2000): texts.append(' '.join(random.choices(['auth','middleware','config','models','utils','handler','service','api','core'], k=random.randint(3,6))))
for _ in range(500): texts.append(' '.join(random.choices(['configure','validate','process','return','check','handle','error','request','response','data','model','function','class'], k=random.randint(10,30))))
for _ in range(1500): texts.append(' '.join(random.choices(['calls','assigns','returns','raises','validate','process','config','handler','error','data'], k=random.randint(5,15))))
random.shuffle(texts)
print(f'{len(texts)} texts, avg len: {sum(len(t) for t in texts)/len(texts):.0f} chars, range: {min(len(t) for t in texts)}-{max(len(t) for t in texts)}')

sorted_texts = sorted(texts, key=len)

configs = [
    ("random bs=256 (current)", texts, 256),
    ("random bs=32", texts, 32),
    ("sorted bs=256", sorted_texts, 256),
    ("sorted bs=32", sorted_texts, 32),
    ("sorted bs=64", sorted_texts, 64),
]

for label, data, bs in configs:
    t0 = time.perf_counter()
    list(m.embed(data, batch_size=bs))
    dt = time.perf_counter() - t0
    print(f'{label:30s}: {dt:6.2f}s  ({len(data)/dt:6.0f} t/s)')
