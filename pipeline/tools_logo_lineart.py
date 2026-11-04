# 로고를 선으로 옮긴 SVG 조각을 만든다 (원본 좌표계 1656x1687)
OUTLINE = [
 "M828 28A815 815 0 1 1 827.9 28Z",                                   # 바깥 원
 "M812 243C668 247 548 360 548 525V800C548 880 590 940 660 968C700 1000 760 1015 812 1015",   # 왼쪽 머리
 "M840 246C990 250 1100 360 1103 520V790C1100 900 990 1012 842 1015",                       # 오른쪽 머리
 "M826 243V1015",                                                     # 가운데 분할
 "M665 558H1040A57 57 0 0 1 1040 672H665A57 57 0 0 1 665 558Z",          # 눈가리개
 "M1078 392L655 935",                                                 # 얼굴 사선
 "M500 548H520V736H500A22 22 0 0 1 478 714V570A22 22 0 0 1 500 548Z",     # 왼쪽 귀
 "M1132 550H1150A20 20 0 0 1 1170 570V716A20 20 0 0 1 1150 736H1132Z",    # 오른쪽 귀
 "M660 975V1022C560 1062 440 1098 398 1130C338 1176 302 1236 298 1276L550 1372",              # 왼쪽 어깨
 "M792 1042L553 1372",                                                # 어깨 사선
 "M990 955V1046C1080 1076 1182 1104 1232 1134C1302 1176 1346 1232 1356 1264L1096 1372",       # 오른쪽 어깨
]
CIRCUIT = [
 "M862 252V386", "M928 270V430L868 492V600", "M980 368V420L912 494", "M1050 468L1036 496",
 "M1072 594V650L1020 704H948", "M1095 700L1036 760H992L945 806V1000", "M862 776L906 820V1008",
 "M985 856V828L1008 806H1090", "M858 888V1012",
 "M855 1042V1400", "M900 1036V1188L862 1228V1400", "M944 1081V1145L905 1185V1340",
 "M1110 1106L1000 1216V1348H1048V1392", "M1150 1120L1040 1230", "M1056 1243H1110L1162 1191",
 "M1000 1282H1040", "M1264 1230L1238 1256H1200V1352", "M1318 1232H1340",
 "M668 1330L610 1390", "M770 1272V1402", "M770 1334V1402", "M690 1226L714 1252V1400",
]
PADS = [(865,402),(980,352),(912,512),(1033,512),(1072,578),(932,708),(862,760),(1035,828),(985,872),(858,872),
        (943,1065),(1095,1192),(1040,1243),(985,1280),(1280,1230),(1240,1285),(680,1318),(770,1255),(770,1318),(905,1356)]
def svg(cls_outline="mk-human", cls_circuit="mk-circuit"):
    parts=[f'<path class="{cls_outline}" d="{d}" pathLength="1" style="--d:{0.1+i*0.08:.2f}s"/>' for i,d in enumerate(OUTLINE)]
    parts+=[f'<path class="{cls_circuit}" d="{d}" pathLength="1" style="--d:{0.9+i*0.04:.2f}s"/>' for i,d in enumerate(CIRCUIT)]
    parts.append('<g class="mk-pads">'+''.join(f'<circle cx="{x}" cy="{y}" r="15"/>' for x,y in PADS)+'</g>')
    return ''.join(parts)
if __name__=="__main__":
    inner=svg()
    html=f'''<!doctype html><meta charset="utf-8"><style>body{{margin:0;background:#9a3007;display:flex;gap:20px;padding:20px}}
    .w{{position:relative;width:600px;height:611px}} img,svg{{position:absolute;inset:0;width:600px;height:611px}}
    svg *{{vector-effect:non-scaling-stroke}} path{{stroke:#00ff88;stroke-width:2px;fill:none}} circle{{fill:#00ff88}}
    .b svg path{{stroke:#fff}} .b svg circle{{fill:#fff}}</style>
    <div class="w"><img src="logo.png"><svg viewBox="0 0 1656 1687">{inner}</svg></div>
    <div class="w b"><svg viewBox="0 0 1656 1687">{inner}</svg></div>'''
    open('compare.html','w').write(html)
