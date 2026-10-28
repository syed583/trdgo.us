$ErrorActionPreference = "Stop"
$AR = "@AR@"; $R = "@R@"; $py = "@PY@"; $pg = "@PG@"
# ---- 0. install the hardened scratch test (SHA-256 checked) ----
$srSha = "e59085338eb3309726169a5fe8bee46ff03173a1c9e51be258ce2bb9f233ba68"
$srB64 = @'
H4sIABTi4WoC/808a3fbtpLf/Suw7PaYbCVGlh9JlKt2VVtpvNexfS252Rzbq1IkKLGmSIak/FhX/31nBgAJUpTj9N4P1z2NJBCP
wWDeM6BhGBc8y+OUM4claewt3TyII+YtFwkLojyG5vGHi7NP7cGnwWcWxq4TsvM4y2cpH/3jhLnhMst5ypzIYymfcyfNOFsEs9Sh
aQb0IMgzlsZhOHXcW5bPecrtra3T4W/DC5bHS3fOM23pHnRnXgxtUZzDnI7HjgbjwS+D0XByeXHC4pThPDzyrq9tHt21mBtHEXdh
jTgKH2FGttN9bXfgvx1oAfgzF6Bx5yyJ07xFALkwbQ4rvGIeDzl826KhCGd8H7H7OL1lfhx6PLXZKedeBrNom955zaZB5KSPshMz
gyjIvWmLJbOJm4f0mQq0wvfsS2ixdptxe2azw971dTLL4zjMdl7jV3h6fQ3TAUoY/CWP+RygljCrWSYAY24njzANnQzO4iy9IG+n
HLeVXV970zbiZZnA97/5Qch/miQp93gSxo82DWq3k1kbVtoMA/tfggH/2m1obHtB2rSWkyTh4/V1cc4ZdJ8BRpv6JnMn44fwCSc8
wU4TOHD3NoPdbJ0lNLqHoEFvtr+32+0wUz8wqwUP6UAa5q5hCbvecp4w04uJesTpIskVRCAJlqblDwnQTTt3piEQw6m9NXwA2nNj
j7MO6zN+x+GICVqWOFnGvRbbgXZHtvkOYBnautCGS+AZIQ3x0IdJlqEnKHgJR2sYxlawoD066SxBLlG/XWAMTpSfqSbA2DwMpurn
H1kcqe9x0SktZsjmyzwI1a8kdHI/ThfF0xiYJS9+LadwEC7Pinmyx+JrzhcJUk7xO1jwLT+NF7D9HCFi8sE5/Nza+nj862QAezeN
bqd7MNnpTLqvJ4MJndEEGcoGEjJazNjdPXh70HGct+7b3Tc7bw98z9t70z3Y6Xbc19yb+t7B3kH34O2u0931XrtO14Xn3R1n9+3e
a2A+zzMssdbk4uzk5JfB4d+fW7TopFZ39903B11n/2Dv7ZsuzO57b97sOa9fv+0cOB34vu9MPa/r7b7hez73YeG9/Y6/dzDd87v8
tbPvwOow5XAEi14ZThRHOKmzhBOP8sAFgvZqDXGKDRlP7wKXT0Dwcfq9TJwpsMLE8RZBVG2BsQ3NSNPOrBhQsKbWBcWj2PUS1quM
TtLgDs5yxr11EHgeNqwHbBUi/ECK5dNkNo2XkSsm94Awp7GTemK5my1gvGUIUhNQc7O1teVxX/CGGTkLYMf4tgU8mAOf9A3D6tEO
5BAbZAjIcPPJoAFGj4khRozfpyCbzPjWwjVpPLSJLytLiMkUlJNpGueD0YgZLPBhLcZDUD7G+8HxCTMs9iPNCB+mbzB29STH31Bv
8UOOMCxLgpYv0whmklsB1jXdBXD4Dz/c3hfgU5+Sj2zsdZXlqelaoA9S5oLeZDDsBjSTk0BvPomXebLM++N0CTvM+YP6SvMqvOWh
WAw6T5AL+6dxxOWqID+EamFZ7gADvoLPOOkJyQaEBjoIJFeWOPcgi4MIVCzqMnw6B30H0q0FcoCdH58DGS9AAJJkmnIGx+gxU4AZ
RDPQfCi27h2YDLeiTQ9qlYN0tN4RPPhAbIrNUFWTnfD++GQIi4N4dTxaD3sBhYL4YrEPHVBgAjpYALodhSuYE6DVUDbinFkQ3QIh
xUAWpsIBMtYUzhLPVzaJM9PwfzT87fTy5ETAlT72Cj5JYLaXnVOWe0HUX5+TnsDCfYSNfvA01fuNxkdnl2NBPT6YBGGora8BXTaq
ndoubJ6bFcJLbPEFNZCiCieagGL2TNKFBTEcOhFgNyC7CQFhYQBoj9DeKYyfHg75mZmfYHh8n+FUuO9p4KGph2IczioAXQft/AH0
oge0kDrRjKONBbTH5hyIYfoIHWK02nAxUPgL21JHdh/kc6lgbPFhWszJWDYpN1w5EoWWzCbe7PeZEeXA2/W/73RzC/efibVGZ5Ph
/xyeXI6OfxsOjo4uLkfDntC7RKo4573zyMzh4PAQSP0eRLIOPaKqJeywIwQ3bbE5WHCCUwjDW3VIsomd8Rw3Fye5Kfc6OjuZjM4O
/z4ct1jRtAYZWAvV+WAuOkvTKA4JpCqtW+0o6UEICYlr/INj4knOzkbDNIUzAkxDS69p6HsHuKTFfAMINorZE/Sz6euqJ34AI3Cc
ZGVISksC93aCsJhgNvrwDE2beyfK+3sl3V0myOq/Y/PvdC5oOtEOMmYiEv0gRSMoI8nSEnICsQ8WNDC+DwxCVmqGVKBJlwwMmBAp
FOWSEHFAyiBuCukAzzzUM+ZVAd4NTlL8EnLh6gYF/9X+/t7e7g18Q74wiahNMi5baGPudSzqtYNNYL+Jj13xsSc+9uXH3u5bOEf8
uJESJsCNzeLYI63XQs1H7A8Hgi4T8wI3t9FquuWPmUlwW+UZoUq8nz/C2JK1LY1ZYHalGs2kpbpr9EGqrnrmCIwalFh1ZgtBnmIP
i/3UpwPtrRH5FKT0rS6JsH9LQCPJI5sL7YQ2IUpEwGFvXeSm6yKXRn1NEapZ5We5BwmPmdpCEqNoMki5iyY4etm0pTHIkD7QAZUs
ghIlir84PfbLybDT2anP7xt/e8ofE25CZ8ueTFA6TSaSV1Y/KR4B6RflE8cNTTSIS7Y4i5iSskjUmuwiJwsNCZ3aSZQ57GI4Gl8c
H46HR9ANHFpmktUFFJuiEcnSYDZHz5kv4jvuWTZ409LfvJ+D7mCOW4h+MnUyAoc82e2BPlO2jatHoPDdGLqxZYR2I7FuHmuA2ezX
4E64S+4SeCrKkY3BvQUdMDo+sgCVaCGicbBEdo2jHExL1O3jD8ejwsWSQJIHoNhXE/r/IYV+/QwM5H2JRjEINQQS3pUBG3YWAZqg
r5Sh+8qPybrP7uhXNDduLJRqQWLCJ8CZm0ZLkgWoOZhpwa/aOzdlH/rcNrbJvFhwZQwqeEkUBZ5N4ijDMzONUXunbVi9dfIp/T2w
LHkKyBd4JPwB7pj5tOAry9iqjAJr2wXyILJiT7AYUJwBtE17Fg8NNDtSQW+4T+proGT/QQwwz44t8/DYek9oGNO/h/TvPzSMXPW6
nU5hoguXc+IFzsxE3xqsdCd3WjJCIkUcPoUuoCOFDdciYkaRVRL+EF1kMETAdIzAVQMpDAQFhpCHlg/79OFzxfeW5O8FAlOEWNie
A9TNwIwCNA2pvxZ/Yeb9PIChoFGEMw/Giw8IvgcnJCu1A0y1QHMP5Qdu2ccvpvH95+8X33vt7z98//H7kaQFBKxPHqypdmiRPQ0z
qA724haaAenIA5mUUWD+ZuBi3tJPMVcIx0zOj28IHIHAEF9WdEQIBTTR3KJFhneewOu2ARcZyChJrNZV52aFWu9JefC2+mJaYjAS
E04H1LEtOGK7POGVcbMuj1WMAWVbVrQS2EphAOjLKMITBPzeoTtbk0MWLEn+mJjFvofjDUM7A+sw3O3ax9klgDWISOKYFsDRLIo3
y+AKOIaAggYso9sovo8MDdkl1GJjKAgR6/hr9Y5MErIHM8FIxjtm2H/EAJhvPCWr3tN2fLute4ugW1eG0N2FskUlTjxw1dvp3lgN
qxttDOcpm3l8eC7sabIzwBCKwGCcY8ivfR0V7EyNyJcB8a8RJHd75H7P43v8VNPhTDQRed9pnMfArv3cTYzNoAjrH62sOCps2XJ9
Az4EFkLaaog7LIDKHYKnDXYyCQykRlrCJMHoGz2BXXSzIxZuBGJweIKqoM7AGG/FNpQwqq2KmYqgw6EWGXHrz3AK6+YlAAjOfcFy
JAdoTVuMaZ7fpwX0bSHKkWmqc4OxQ6LXpWhJIBSVU93aJvB1BP2F2b+CnDJ8kC+z6rwFHgr5DvJQxhts/sANVDxiIBHK0QtXFFYQ
LiYl3YuWlaOKhYvRz9D/x8BN4yz2c3YE3kCEGEQ1LKKqoNoj/IL4LGAy0QJqk630ZQk67B1qJNJjoGncOfKfV+XgQlxdAYz3ABLK
P8LHaXyexujl06/DeLEAmsfvIIrZE/uV5+2PyXiOOYcjBRX7k33CREj7bPoHtEC3/5zYFzyLl6mLKFuQwtxW8P4pEgx/ikP5UyrU
bbaCeUYclbmaqP2ePLB9Zmjxwj/Ze1AjTt4+QVtRgHJ81GLnwk4/pchbsXqLHcNqgRMW0I5Bj8FaLgH1xLZRezt3ThCiDdlj24Ah
AL8Q9fZHmNSZwRDjprTtDzry/DC6g0eN6jeKsyCz84fcsOz7NMgx0/EAllspsfCwgQ44RkXgfPrGMvfbbwC95L5mfQPDlo7LpZZA
+UZmJsgq05B2bRjPRCyTSBqWF3IYUUq/NIvOV8YBcSuAiZPpzpdvkyEA0rEW1KEovO3GyaPpUxRPDhZwgbFcTE2MoxP7FxwX+YXp
iT8allGIqw2zue/jOd3xZxFZin4aRKFj6qMQGcBppIDHNRUQKhuDhDkefqhZ0aV1/J1hNZxUNba6zKURGvqTbO7ojpTsIrMfYGA4
3f0DISKolw5yfQ1bUgFsOL2mwDX8a9nUDxplL8ue8wcvACUN9lZhDCePE1xTklrp1KUgR9pgUHAUNKhO0OHBT+wNasahQAd/cFwR
LfWWIn5O8dCYmRloTY4BtJyjvARwgbXAG5/l8zaGLYIHeJphpoql8T3F5xY8nXGZdxVhq9LGnQP5KNwsvH2zpPfphBQ6LVfZBQ2z
lwkQHDcxDjCdWHYeTxCkzHwDWJoGM/Km4UHlDBoQBTCCsez7sMIdiAtMKcJ2gkWQ93dLrH1chnmQcUTWAjRqgMFfE7wjsKM1BIFN
maK3LNKukYwUUxIPrAXyTF9VHuJqNogohA4tLJH0EJDIHwKgDEztkGuuAbg1gJ++nmezDxEentJOrFbjI5zNKhxquVKxDmUB2zAx
Td5mjvBCaG30B0xD7QZIMUU7snMjos4pGVEYlSrmBeeM0HiDASsYigt8ZRhBVwyrJiYW5cz2nRMukYthk8UDQqX+gKDWuUGwPqbV
CTcaT4wxzYnHs52JA8Rs8tn5Z4rlIKCgZEQgkGKxsqHMhou8DcpzjC8LpmAJqGuYzK5ElXF1212mQNUgZYRUNWipZDkFKrI9vogn
YCl7qM2ftltsW8g5AnhlsfEZE9F5g0LSblKyxDSMp3BKU0OKRsEODwLTDyQhkwo7XGnCEwdLl21KQoYkJEY1YL4bPYT9y+fxcNRD
9va4EFct/BHx+5DCA2DlZyG5OhL5eDITRKuJi5RYvyBWwaRVuWVKy/6uYbbdRrVCJs3vMjfTwo0TFHDKCDE7Oz35jD4CQI6KAWRU
nlbFvcgAOSHIJTEEel/+2O10QVpc/tjpvNkXicjfxi32/j2zbZsJD70kg6J4g2euk3BRcuHQs/YCk+tJkHCcV8XMCD1chviReAan
o2ORh0/QjBDFJaTVMoW+rMxC4Im3iBxbKqXRZ5g8a1F8lkLhhawk1DcdYyVvg71AeXhCsUGHFDqwhjSFdgASvsOLk/fa5t7BzkFv
hdCugCNDHzUHoYcyLIQ8kyLjIYj4NtYniBRZ6txXzAyCv08fV732zo0ONeJcrFE1TRYwIOU22ZRmOhVsZP7cE5x0bVs/65R1bZr2
Dz9b1xZ7f3H2UeTG3hktWnItuLxYDyaL4ygO4sotbAVxzqaWflvYszReJuaOVZxFy7Dg0DDGUqY9QnkkmDBC0rWN6rJl/Fp0z2oI
QNpQDkO5DcncJflINlw4GMgQMzgk6mXJhj1IZ0v0Js7xVyo1sJPYjudNHPkMPRIlwfmXZQA6QIsYNXQWRTkv7i7Lcl7cH0tu6p1b
mNpL+gaaVShYGmpzmIkZZLQ0RbFLIKLYzszBDG+1pAaEs2Vs3h/YJQAABo/6IPyxIsB3wErodxQYWGyD6SdWZpAogKHkQ8gpmd2Y
7jTlbM8AgJa8US5b+p2y3MWe8Ry/Y8jPIrs8T71ZPKlVF4E3usUa/sQeasFvsyFa+U6B0FsLsYM6RQjk6JYUqWVkHtUtRzZyc4qx
YrYA7UUYu3nf6F9JUlF7T43DXq2ISu0SexvqRO7RNxWGl3DS8sDNqkl8zOfdYoTPFIJKbviZc/D4dAm0jn43uYmwmkNWF2BPFLkB
aSoQCp4bXZ6fXwxHo69M68ZpukyQ0Nx5HACR9K8M4cdTkO3eSdAX3TA1ifKUo09FcQqMIPZATi9Q+yAWRocXg/HhByJ1kdUUXEG1
FIiRTcBhOVrzPjfup1KV1sg4VBgihqN0SmzaDk6SSZmkasGyR4B4Rk1T5X06NigtDLeIcHEch/DkKepBj1fMN56iFcVeULqb1Sar
8ElF8BSfRsLjFu62cKlLV1t+UwyEv7Aoy1rpORYnDCU/avNT9QkZ/wBfaa1qxR1UAGSMxmfn58OjHhNLA0Ay0PVKV82vqAhTrQaT
L7EqFQlwupZq7FKDNPIlwkpngEKARbsIHMIqcXinajlgV0oYGFIj0yhKftkhho7MZ/ZRD59SLIPIDL1PEL5su4j+mJnj87yQvWub
ULEQsP7mDp0QldG1WLWarmr6IMim3B8gTeVHKJJRng9aUsKB39AVDWJ4XNXDYrN+udsn7LtC5bIIgPXAGUA/WtmPc0dmI1N+F/B7
kHyjD4N2d/8AcyDOymgsmhBbJ9kmU1lSu4jiBqD+os6DeKQv+l51RDkBlTIQdSOXqRMV5TIZtW0+OzDuZV2NEBFgtwboeosULcrr
KTANEV+lUAfo8AqT/Zjo35yLeHNzs+GcxZ7kTrZ0ROuVtCpLAlwNerWNMo5XATFEsdw7lt0GYCx5MOKrcCmSiW9vViQznOjRFA00
ZCKGTIoBVlF0h5kJRbsSlSXPIlKoYgsFwJUuRW5qQWFZGaCszBaGO5FhcQbydQUL+Ib5RD8xkk2Bq0kW/B/Khh1+0LN3/BX7+IsC
B/ZBPLseg5NhvnSRp5yLIGEhGBrShpXdaXyNwInMq/EnfC0LC2hGMYr8hL6QBhSQx4Yi30mxpb7u3VA58kRUz/RL8l0r0NDQKgX2
jQrrizSw0b4kpSmlGDYMRDwb5BD9bBcBuMvx+zeiKYrb2WPkGhqVmgp2PdaqhydfUNfx8tCvJsBEEapRK/qWdf+eqte3kMa0mjt0
L8AorUCgALSu2jsHnR4JicoYRc1VSVc7DIkA2Ww0ya0yHwrWESaVfaOdsCfB2yvWdqXROwF7Ae0UsHDKew7w1Efk92Pfxx/4fZ7G
UbzMJm68wBgdPCH+NvD5NJ7BkwzsXsTrjmBcmyypyRRLikX1jNpbST4uAFZCigweiLKBCSY7gMd5BIZMivFGEqXKa/4OTBVKzqbc
X2bgDoPTizLybMRMqseTJYm5g6YtPBT1eRZr/4QETH0jijEB25VyTv1VJCCCUnn6b4zQAqlY/lvKOrSdbkTWLRUcRzniNn1/2zHq
/BpKcYK8Vs15tLFKBhFw09IkSZEHWXPoERjggudqxIpQRDzDgryGda2G7ELBq2RYNg6qWpeG0Wg7KOmHmosO/Umd+UqyFzOlDYgF
y+wpdVdYwgBLVMuDZMQLa4KuesDbxNpaLwHFNih1aNxeraPKQIIubDwcuWbdNSOvlN4S2y8UX2qcrrMpJk0JWXpGNSaEFUnnRIlq
IO3IN+ro+Yrw8qu15+WwyiCvjLGvFxYJYIrSIuCMorhIX6xij+uHfnQ8+PX0bDQ+Phyx0eC34REFmR9ZtkzvipK1kDvRMsGz9uqn
Vc0KVvOAOgmun5xfauBKJrAxI0iRSr9mYfQaowZqZ5i+llawGTpgJor8DaaeDeuZkWViz3+O2Wqk3t3v3TTXGpdCHe+C0KnPwe3X
RCKeYL8Qnxi16JcRILqBN1Gp3p1OSRsU1JWOqC07mrQGmH/Mm+Le+yqjia7yEiteSMKSAUXB+yoOXfRG3SXoGOPwYjgYD4sbgiqJ
JOJYGgK9LMKIf23Zau/nQYYZXgQcZWsUrdHFoYYQabEBX+0AewJXCUo4PTs5+/X4VKqXXz7jLZeLkxHxclrNbj9zxciqXW8po9Yq
o6Z8SoxaExSjww/DjwOZYulJhqdUg0OcTAj8dDz+cHY5xhsnxVU9lEKWdulD+N1YS4FcKSP44pJnMA35puM8ujg7rwLBDgejw8HR
UDvL75A52qxMfLTbZQF1R1UBUgWgtcnwXfMnwHqN7yNRWkq/ivtTouiFzGC9bB+MCaMUbA0mM5WAVClMOCSagQz8SvWDWn6pbv9u
zsbfbNWFiRby8LGAb47+WcSeNISwNuCoZ3f8VdZSolwzZ1ct0JQ8QkGSWSu2kIUctLx2BAW02I/K5JocfdTV6N2E0ON1R9u21G8a
uJgtFgFTYj6uXcQUNQfC5mcmuJfyKyqOrLh42WjLF/UK6JQaNEwUsSlNXd3IWoDXN5oxhLnUbZpu+7npVhLSOva+Vb4osueLZYjh
hJHk8568ono6/FQkzTP+ZQkOE0kIyUGBrDAW1bmIsCP0LAfnx3grm2dobsvQ4ia+HJyMhxfsaPh+cHkyZucXx78dnwx/HY7Y8WmN
XX+9GJyO2eDkhJ2dMhDKeHFyfIbVDGJ75a3JFqvIL+tfvPZo+I/L4enhty+vWe4yqNxjL/j7TtQrUCxcVmHqCZLK7v4pBdMMI92l
kgHvZotDQ+nl+RFKez3rNxqOwXcDKxovjrpoAOi/fmQg9jqdHfbpw/BiyKicfr/TabBQKPnGXv73nagUoAIYLL0glqUmMCaXCypo
yHosv49FwQFG8hG5QSoAZATgX9hw3rDlBzsRSU9zNDwZHoKKAzI5RLsCdgxbP2U7HTbGT9VBH08j9SU0ZO3sWGx4AhN988AODDw9
YoMRS57pCLxg7nRauI7FHmR7btMUD/DxAunTRGtzB9WThkgJfR6D5J65YLBm5vZ6Qca2xY5HYMWMGd6iBB3mc+CDOOJU5V5ldKEK
CotEKoRMvXxC8VBhfSB96CgQtjemjZysxSo68C+rN80Vg1nX2WnNZsY/sKSmjcg6PLs8HZs/yHw6Oh+IN+ay/z6DY4PfdHknAXud
RSi5Ijumc3MB8LB8Js40sqMsEZcnmcT8NhsAgVDvWwyxwIN0+1mkR2mY/fuAWrYhhwNAaZA/Pg9/QkmrF8APHQM3KDglA3JbODWY
nl1KmVZFdZqYAlMXcN4rJl8eUWhbIDDE7kqwGWyoHYJKAN9Z7gufA1ArpiBbJzvHFinAiZpbZEOquYcG/qmBSMyL7WI2aI+Wiylq
J58VyUVBtP36mi3K+tEG7zKMJVSe1r1rZZCDDAdk8RnusyfYmQqC7rJ6cV/FZi9v5TTd1tbLsswNxntR+SSsd1qjrwkJavbpX2mD
W82381UygTZFZWFb66UtHimivlauVbmyWJdqmqxK1T3sKRdvtsErNnjLj/Yv7y7I4hFx5QaL2UjkodSihS32Ewap4XzKphXNXDsV
LACcqBtKel2fLOqrctTEe4nw0nH6AqlenHqp3sE8daTZjhsVdYTiGZIjgdHXdisoEZoFJWp7rm2XHF+qzO1XanoVEur7VbWjetko
+oh6lasYKU+88YSFAV7ZZ5AR5bQDT9iaobK6iSFMUS+8WC+TbekFxIQQrKZoqjkRwGsejqxJVb/L7UuK6fdL9DRN2EBMLbbw9tlT
MUwEEp9bWQYXn0SfVYEUYkwUe9R3RWigtnfiJv6TwD2eZxmuwRvHX0J6d4JZpLBr4TmULVjZuzFb/Uy9eDV7kIErkH8tod2nhPY/
Y8d3G8R3t+A42k4NByTJxFHWNv+8Fbe70WYpaUMXCbtSJtSWh37izTrftvre5tX3XmRKghYBGeM8TrJgFjmhgOErRmUF8KJurQ44
j+5QcgcAbpzh68OCFDlPf8NY39cuWPRevZIW6H+V+XoVBH1Vie78nGUhlrf2vSDDY6sRWePbSB4ziQ8cgNEkLMJriRseKmQECi2N
41y/REaNInxdbZO1Qyim26QSRIeSuUHvbX3FI/vaXX1AWh/+b4wi628wabFEatOteuhOe0Fcq3gzHNYotunNYlXzQEpaogFpPjjT
DCvnptxHM1+bDVWIMNoV3VqbX0RSCJgfqB6mUb5XXmUHsKEpGdfuK2ivzROWlyHqV636ezte/lqCfwEE8g0gePz0UoNKIqAjU15f
SQWkbktaZhpDrQcBqfpCm37tfS+hfk1IvLQJCRvf1ISf9/NYvIcFf3wEb2AwPrv4bFgN6ZgysFiWpj4XYqQwTJ7SpRseIaqKYfRK
t4yZFyejFiujva0iFokXSluiqAeE0mOrYsoBGKDHKeroyrqBIlro6raJBKQyds1CoCrRGiHrsr+iwcu5axRcVnTVVy/eviheJiHs
L42jnuObb9gAEaVc6i/tQK806GLVUDPlNbBGYVW3BZPIylusvld7R4NFHrq4I60ogYqa6SS7pWnTiIs6uOyf+fuOAf4WSZxjvPa5
vQG6i56Yc+GgcT0SBo+WCHwoSMVt6+fR/s3CSLnA4+FozIYXF2cXzGx6W4pFrzrYJG7w/ROrjZWKDe/OkknrWupvQ5lEnJCmXOC/
viOrlFSFxFrpA75aL6nVP6B6eNmp0Ssmtde5hPGshXHfqCA5pDI4pwXsyXpXvuuMb3ojWmWLNH1Zeqq9o8LABPEELAdjbcBL31jR
nBD/9muy2jmZWnrc2nA5tumSrDasJTat35f9tldIEJkC0uuFFI5NEyNXVOr5qKUs032+RLe57K/FxH3Zicy5r+NY6qr1Ko4Yiytl
IJOSXpIMpw69Uyotb/yp9yvK7aRX+HpEeeFPseV1RM6b7IoJPrrp6XjgyanXlj6VTbIErXgZinipXq2kWb4/AYu/JfhtaWy0xTrt
p+qrVra1V61sWysb31lqVJbQy/7wKRVVZ+aTuO7Y00s1DbkXaJXfVnihCJVNv7vxhrFEtxiB9ZVq5cptnx3EJSKa3NXO1tYW/FYi
jJIokwleAJpMZBpF2OggXMW1IGvr/wFG4pIxM1kAAA==
'@
$gz = [Convert]::FromBase64String(($srB64 -replace "\s", ""))
$ms = New-Object IO.MemoryStream(,$gz); $gs = New-Object IO.Compression.GZipStream($ms, [IO.Compression.CompressionMode]::Decompress); $out = New-Object IO.MemoryStream; $gs.CopyTo($out)
$srBytes = $out.ToArray()
$h0 = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($srBytes)).Replace("-", "").ToLower()
if ($h0 -ne $srSha) { Write-Host ("STOPPED: scratch test altered while pasting (" + $h0 + ")") -ForegroundColor Red; return }
[IO.File]::WriteAllBytes((Join-Path $AR "phaseC\scratch_restore_test.py"), $srBytes)
Write-Host ("scratch_restore_test.py installed, SHA-256 " + $h0)
# ---- 1. which PostgreSQL 17 tools exist (read-only) ----
$need = "initdb", "pg_ctl", "pg_restore", "psql", "postgres"
$rep = $need | ForEach-Object { [pscustomobject]@{ tool = $_; present = (Test-Path (Join-Path $pg ($_ + ".exe"))) } }
Write-Host ("tool folder: " + $pg); $rep | Format-Table -AutoSize | Out-String | Write-Host
$missing = @($rep | Where-Object { -not $_.present } | ForEach-Object { $_.tool })
if ($missing.Count -gt 0) { Write-Host ("MISSING in " + $pg + ": " + ($missing -join ", ") + " -- nothing was run.") -ForegroundColor Red; return }
Write-Host ((& (Join-Path $pg "pg_restore.exe") --version) + "  /  " + (& (Join-Path $pg "initdb.exe") --version))
# ---- 2. the read-only fingerprint helper (SHA-256 checked) ----
$fpSha = "@FPSHA@"
$fpSrc = @'
@FPSRC@
'@
$fpText = ($fpSrc -replace "`r`n", "`n") + "`n"
$fpBytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($fpText)
$h = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($fpBytes)).Replace("-", "").ToLower()
if ($h -ne $fpSha) { Write-Host ("STOPPED: fingerprint helper altered while pasting (" + $h + ")") -ForegroundColor Red; return }
$fp = Join-Path $AR "phaseC\prod_fingerprint.py"; [IO.File]::WriteAllBytes($fp, $fpBytes)
# ---- 3. the newest validated dump and its SHA-256 ----
$d = Get-ChildItem (Join-Path $AR "db-backups") -Filter "*_predeploy.dump" | Sort-Object LastWriteTime | Select-Object -Last 1
$sha = (Get-FileHash $d.FullName -Algorithm SHA256).Hash.ToLower()
$side = ((Get-Content ($d.FullName + ".sha256") -Raw).Trim() -split "\s+")[0].ToLower()
Write-Host ("dump: " + $d.Name + "  " + [math]::Round($d.Length / 1MB, 1) + " MB  SHA-256 " + $sha + "  sidecar match: " + ($sha -eq $side))
if ($sha -ne $side) { Write-Host "STOPPED: the dump does not match its recorded SHA-256" -ForegroundColor Red; return }
# ---- 4. production fingerprint BEFORE (read-only) ----
$beforeText = & $py $fp $R
Write-Host ("production BEFORE: " + $beforeText)
# ---- 5. the scratch restore (isolated local cluster 127.0.0.1, first bindable port from 54320; never reads production settings) ----
& $py (Join-Path $AR "phaseC\scratch_restore_test.py") --dump $d.FullName --pg-bin $pg --sql-dir (Join-Path $AR "apply\migrations") --gate (Join-Path $AR "phaseC\prod_gate_checks.py") --diag-dir (Join-Path $AR "scratch_diag") --expect-tables 55
$rc = $LASTEXITCODE
Write-Host ("scratch restore test exit code: " + $rc)
# ---- 6. production fingerprint AFTER, and the comparison ----
$afterText = & $py $fp $R
Write-Host ("production AFTER:  " + $afterText)
$b = $beforeText | ConvertFrom-Json; $a = $afterText | ConvertFrom-Json
$same = $true
foreach ($p in $b.PSObject.Properties) {
    if ($p.Name -in @("demo_trades_rows", "demo_trades_max_id")) { continue }
    if ($p.Value -ne $a.($p.Name)) { Write-Host ("CHANGED: " + $p.Name + "  " + $p.Value + " -> " + $a.($p.Name)) -ForegroundColor Red; $same = $false }
}
$grew = ($a.demo_trades_rows -ge $b.demo_trades_rows)
Write-Host ("production structure unchanged (tables, columns, indexes, roles, policies, RLS, ACL, no audit objects, no scratch database): " + $same)
Write-Host ("demo_trades rows " + $b.demo_trades_rows + " -> " + $a.demo_trades_rows + "  (never fewer: " + $grew + ")")
Write-Host ("scratch folder removed: " + (-not (Test-Path (Join-Path $env:TEMP "trdgo_scratch_restore"))))
