"""Original Matrica particle engravings. Pure vector, deterministic, no raster assets.

Run: python3 scripts/generate_particle_art.py
Every illustration has its own construction; shared ink, grain and node grammar
make a family without repeating a single motif across topic cards.
"""
from pathlib import Path
from collections import defaultdict
import math
import random
import html

OUT = Path(__file__).resolve().parents[1] / 'app/static/particles'
TAU = math.tau
INK = ('#756549', '#a68a57', '#d3b47b', '#f0d6a1', '#ddd9cf')

class Engraving:
    def __init__(self, name, description, seed, size=640):
        self.name, self.description, self.size = name, description, size
        self.rng = random.Random(seed)
        self.dots = defaultdict(list)
        self.lines = []
        self.accents = []

    def dot(self, x, y, strength=0.65, radius=None, silver=False):
        if not (8 < x < self.size-8 and 8 < y < self.size-8):
            return
        r = radius or self.rng.choice((0.65, 0.8, 1.0, 1.3, 1.65))
        if self.size == 640:
            r = round(r * 1.35, 3)
        colour = 4 if silver else min(3, max(0, int(strength * 3.9)))
        opacity = (0.42, 0.65, 0.86, 1.0)[min(3, int(strength * 4))]
        self.dots[(colour, opacity, r)].append((round(x, 2), round(y, 2)))

    def line(self, points, opacity=0.26, width=0.65, colour=2, closed=False):
        if not points:
            return
        d = 'M' + ' L'.join(f'{x:.2f} {y:.2f}' for x, y in points)
        if closed:
            d += 'Z'
        self.lines.append(f'<path d="{d}" stroke="{INK[colour]}" stroke-width="{width}" opacity="{opacity}" fill="none"/>')

    def node(self, x, y, scale=1):
        # A broken diamond and a displaced satellite are the family signature.
        self.accents.append(f'<g transform="translate({x:.2f} {y:.2f}) scale({scale})"><circle r="11" fill="#d3b47b" opacity=".04"/><circle r="5" fill="#d3b47b" opacity=".1"/><path d="M0 -7L4 0 0 7 -4 0Z" fill="none" stroke="#d3b47b" stroke-width=".65"/><circle r="1.8" fill="#f0d6a1"/><circle cx="9" cy="-8" r=".7" fill="#ddd9cf"/></g>')

    def dust(self, count=75, center=(320,320), extent=(265,230)):
        for _ in range(count):
            t = self.rng.uniform(0, TAU)
            r = self.rng.uniform(.65, 1)
            self.dot(center[0]+math.cos(t)*extent[0]*r,
                     center[1]+math.sin(t)*extent[1]*r,
                     self.rng.uniform(.1,.48), self.rng.choice((.55,.7,1.05)))

    def save(self, filename):
        parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.size} {self.size}" fill="none" role="img" aria-labelledby="title desc">',
                 f'<title id="title">Матрица · {html.escape(self.name)}</title><desc id="desc">{html.escape(self.description)} Авторская серия «Звёздная гравюра». Векторные частицы, без растровых вставок.</desc>',
                 '<metadata>Created for Matrica, September 2026. Source: scripts/generate_particle_art.py</metadata>',
                 '<g id="engraved-filaments">', *self.lines, '</g><g id="particle-field" stroke-linecap="round">']
        for (colour, opacity, radius), points in sorted(self.dots.items()):
            # Tiny rounded strokes are compact, editable vector particles.
            d = ''.join(f'M{x:g} {y:g}h.01' for x,y in points)
            parts.append(f'<path stroke="{INK[colour]}" stroke-width="{radius*2:g}" opacity="{opacity}" d="{d}"/>')
        parts += ['</g><g id="signature-nodes">', *self.accents, '</g></svg>']
        (OUT/filename).write_text('\n'.join(parts), encoding='utf-8')
        return {'file':filename,'name':self.name,'particles':sum(map(len,self.dots.values())),'bytes':(OUT/filename).stat().st_size}


def hero():
    a=Engraving('Рождение света','Асимметричный вихрь золотой пыли вокруг тихого тёмного ядра.',91014,800)
    rng=a.rng
    def ribbon(t,u=0):
        r=230+18*math.cos(3*t+.4)+u
        x=r*math.cos(t)
        y=(r*.73)*math.sin(t)+30*math.sin(2*t)
        theta=-.43
        return (400+x*math.cos(theta)-y*math.sin(theta), 390+x*math.sin(theta)+y*math.cos(theta))
    for band in range(25):
        u=(band-12)*3.25
        for i in range(154):
            t=TAU*(i/154)+rng.uniform(-.01,.01)
            if rng.random() < .17+.09*math.sin(t*2): continue
            x,y=ribbon(t,u+rng.gauss(0,2.2))
            strength=.25+.53*(.5+.5*math.cos(t+.9))+.13*math.cos(u*.06)
            a.dot(x,y,strength, rng.choice((.65,.8,1,1.3,1.65)), silver=(i+band)%19==0)
    for u in (-47,-39,-23,5,28,48):
        pts=[ribbon(-2.1+i*4.4/130,u) for i in range(131)]
        a.line(pts,.11 if abs(u)<30 else .23,.5)
    # One descending comet-like seam, asymmetric to the orbital field.
    for strand in range(9):
        for i in range(93):
            t=i/92
            x=318+132*t+35*math.sin(t*5.2)+strand*2.3
            y=137+499*t
            if rng.random()>.26:
                a.dot(x+rng.gauss(0,1.4),y,.28+.35*math.sin(t*math.pi),.7,silver=strand%3==0)
    a.line([(397,278),(421,326),(394,372),(417,424),(402,479)],.28,.6,4)
    a.node(397,278,.8);a.node(402,479,1)
    x,y=ribbon(4.9,12);a.node(x,y,1.25)
    a.dust(155,(400,390),(342,318))
    return a.save('origin.svg')


def self_portrait():
    a=Engraving('Внутренний свет','Неповторяющийся рисунок линий, похожий на отпечаток света.', 1101)
    for ridge in range(22):
        r=25+ridge*8
        pts=[]
        for i in range(112):
            t=-2.2+i*5.68/111
            warp=1+.065*math.sin(3*t+ridge*.13)
            x=316+math.cos(t)*r*warp
            y=328+math.sin(t)*r*1.08+18*math.cos(t*2+ridge*.14)
            pts.append((x,y))
            if a.rng.random()>.13:
                a.dot(x+a.rng.gauss(0,1.2),y,.3+.54*(.5+.5*math.cos(t+.8)), a.rng.choice((.65,.8,1,1.3)))
        if ridge%4==0:a.line(pts,.16,.5)
    a.node(328,322,1.3)
    a.node(453,170,.85)
    a.line([(325,290),(333,279),(327,264)],.25,.65)
    a.dust(85)
    return a.save('self.svg')


def relationships():
    a=Engraving('Нити близости','Два самостоятельных потока встречаются и создают общее пространство.',2202)
    for side in (-1,1):
        for strand in range(21):
            pts=[]
            for i in range(115):
                t=i/114
                spread=(strand-10)*3.2
                x=320+side*(148*math.sin(t*TAU-.9)+spread)
                y=118+t*392+spread*.55*math.cos(t*TAU)
                pts.append((x,y))
                if a.rng.random()>.2:
                    a.dot(x+a.rng.gauss(0,1.4),y,.27+.52*math.sin(t*math.pi),a.rng.choice((.65,.8,1,1.3)),silver=side==1 and strand%3==0)
            if strand in (2,10,18):a.line(pts,.16,.55,4 if side==1 else 2)
    a.node(304,288,1.1);a.node(340,344,.9)
    a.line([(308,295),(338,338)],.5,.6)
    a.dust(65)
    return a.save('relationships.svg')


def vocation():
    a=Engraving('Своё направление','Раскрывающийся веер световых нитей — движение от опоры к возможностям.',3303)
    for strand in range(32):
        pts=[]
        offset=(strand-15.5)*4.2
        for i in range(115):
            t=i/114
            x=147+335*t+offset*math.sin(t*math.pi*.92)
            y=491-337*t-85*math.sin(t*math.pi)+offset*math.cos(t*math.pi*.72)
            pts.append((x,y))
            if a.rng.random()>.17:
                a.dot(x+a.rng.gauss(0,1.9),y,.25+.59*t,a.rng.choice((.65,.8,1,1.3)),silver=strand%8==0)
        if strand%6==0:a.line(pts,.18,.55)
    for k in range(8):
        pts=[]
        for i in range(68):
            t=i/67
            x=128+350*t;y=480-k*15-45*math.sin(t*math.pi)
            pts.append((x,y))
            if a.rng.random()>.3:a.dot(x,y,.2,.65)
        if k==0:a.line(pts,.18)
    a.node(154,482,1.05);a.node(482,154,1.2)
    a.dust(65)
    return a.save('vocation.svg')


def rhythm():
    a=Engraving('Ритм времени','Две чаши из частиц переходят одна в другую через светящуюся точку настоящего.',4404)
    for strand in range(31):
        phi=TAU*strand/31
        pts=[]
        for i in range(92):
            t=i/91
            y=126+388*t
            waist=(.1+abs(2*t-1)**1.28)*142
            x=320+math.cos(phi+.7*math.sin(t*math.pi))*waist
            y+=math.sin(phi)*26
            pts.append((x,y))
            if a.rng.random()>.13:
                a.dot(x+a.rng.gauss(0,1.4),y,.22+.56*(.5+.5*math.sin(phi)),a.rng.choice((.65,.8,1,1.3)),silver=strand%5==0)
        if strand%7==0:a.line(pts,.16,.5)
    for cy in (128,513):
        for band in range(4):
            pts=[]
            for i in range(110):
                t=TAU*i/110
                x=320+(157+band*3)*math.cos(t);y=cy+(28+band*2)*math.sin(t)
                pts.append((x,y));a.dot(x,y,.35+band*.05,.8)
            if band==0:a.line(pts,.3,.6,closed=True)
    a.node(320,320,1.4)
    a.dust(70)
    return a.save('rhythm.svg')


def resonance():
    a=Engraving('Созвучие','Два узла создают связанный узор, сохраняя собственный ритм.',5505)
    for band in range(21):
        width=(band-10)*2.8
        pts=[]
        for i in range(180):
            t=TAU*i/179
            r=132+55*math.cos(3*t+.5)+width
            x=320+r*math.cos(t)*1.08
            y=320+r*math.sin(t)*.93
            theta=.25
            x,y=320+(x-320)*math.cos(theta)-(y-320)*math.sin(theta),320+(x-320)*math.sin(theta)+(y-320)*math.cos(theta)
            pts.append((x,y))
            if a.rng.random()>.2:a.dot(x+a.rng.gauss(0,1.3),y,.32+.48*(.5+.5*math.cos(t*2)),a.rng.choice((.65,.8,1,1.3)),silver=band%6==0)
        if band%6==0:a.line(pts,.18,.5,closed=True)
    a.line([(278,290),(312,316),(366,348)],.32,.7)
    a.node(278,290,1.2);a.node(366,348,1.2)
    a.dust(65)
    return a.save('resonance.svg')


def moment():
    a=Engraving('Точка события','Луч пересекает волны времени в одном выбранном моменте.',6606)
    for ring in range(21):
        r=27+ring*9
        pts=[]
        for i in range(115):
            t=-2.52+5.02*i/114
            x=287+r*math.cos(t)*.91
            y=314+r*math.sin(t)
            pts.append((x,y))
            if a.rng.random()>.15:a.dot(x+a.rng.gauss(0,1.1),y,.23+.56*(.5+.5*math.cos(t)),a.rng.choice((.65,.8,1,1.3)),silver=ring%7==0)
        if ring%5==0:a.line(pts,.16,.6)
    for strand in range(9):
        for i in range(130):
            t=i/129;x=149+369*t;y=486-332*t+(strand-4)*2.1
            if a.rng.random()>.1:a.dot(x,y,.36+.41*t,.8)
    a.node(365,292,1.35)
    a.line([(142,492),(524,148)],.35,.7,4)
    a.dust(65)
    return a.save('moment.svg')


def mark():
    # Small-size companion to the particle grammar; no detail that collapses at 31px.
    name='mark.svg'
    svg='''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none"><title>Матрица · знак светового узла</title><path d="M11 46L22 15 32 36 43 15 53 46M22 15L32 50 43 15" stroke="#d3b47b" stroke-width="1.15" stroke-linecap="round" stroke-linejoin="round"/><path d="M8 34A25 25 0 0 1 34 7M56 31A25 25 0 0 1 30 57" stroke="#a68a57" stroke-width=".8" stroke-dasharray=".1 3.6" stroke-linecap="round"/><g fill="#f0d6a1"><circle cx="22" cy="15" r="2"/><circle cx="43" cy="15" r="2"/><circle cx="32" cy="36" r="2.2"/><circle cx="32" cy="50" r="1.4"/><circle cx="11" cy="46" r="1.4"/><circle cx="53" cy="46" r="1.4"/></g></svg>'''
    (OUT/name).write_text(svg)
    return {'file':name,'name':'Световой узел','particles':0,'bytes':len(svg.encode())}

if __name__=='__main__':
    import json
    OUT.mkdir(parents=True,exist_ok=True)
    manifest=[hero(),self_portrait(),relationships(),vocation(),rhythm(),resonance(),moment(),mark()]
    (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    for entry in manifest:print(entry)
