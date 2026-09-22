# Uživatelská příručka

Evidence zpracování materiálu — návod pro obsluhu.

Aplikace je určená především pro mobilní telefon. Otevřete ji v prohlížeči na
adrese, kterou vám dal správce, a přihlaste se uživatelským jménem a heslem.

> **Změna oproti dřívější verzi:** aplikace už **nevede stav skladu**. Příjem,
> Výdej, Ruční úprava, přehled Sklad i Historie pohybů byly odstraněny.
> Zapisují se zakázky — co se zpracovalo, kdo na tom dělal a jak dlouho běžely
> stroje. Aplikace nehlídá, jestli je materiálu dost; zapíše, co zadáte.

---

## Obsah

- [Přihlášení a změna hesla](#přihlášení-a-změna-hesla)
- [Spodní menu](#spodní-menu)
- [Zpracování](#zpracování)
- [Schvalování](#schvalování)
- [Přehled zpracování (pro vedoucí)](#přehled-zpracování-pro-vedoucí)
- [Stroje](#stroje)
- [Materiál](#materiál)
- [Hodiny](#hodiny)
- [Stažení do Excelu](#stažení-do-excelu)
- [Časté potíže](#časté-potíže)

---

## Přihlášení a změna hesla

Nové heslo dostanete od správce. Je dočasné — **hned po prvním přihlášení si ho
změňte**: nahoře vpravo klepněte na **Změnit heslo**, zadejte současné heslo
a dvakrát nové.

Odhlásíte se odkazem **Odhlásit se** vpravo nahoře.

## Spodní menu

Ve spodní části obrazovky je menu, ze kterého se dostanete všude:

| Tlačítko | K čemu slouží |
|---|---|
| **Zpracování** | Z jednoho materiálu vznikl jiný — hlavní formulář |
| **Hodiny** | Odpracované hodiny lidí |
| **Přehled** | Všechna zapsaná zpracování ke schválení — vidí jen vedoucí |
| **Stroje** | Motohodiny, tuny, sazby, cena a tankování strojů — vidí jen vedoucí |
| **Materiál** | Kolik tun se kterého materiálu spotřebovalo a vyrobilo — vidí jen vedoucí |

Po přihlášení se rovnou otevře **Zpracování**. Stejně tak se tam vrátíte
klepnutím na logo vlevo nahoře.

## Zpracování

Použijte, když se **z jednoho materiálu stane jiný** — drcení, třídění, řezání.

Formulář má čtyři části:

**1. Popis a vaše hodiny**

- **Popis** — krátce, o jakou práci šlo. Například *Drcení kameniva na frakci 8/16*.
  Toto pole je **povinné**: podle popisu se zakázka pozná ve všech přehledech
  i ve stažených souborech, takže jedna věta stačí, ale nechat ho prázdné nejde.
- **Poznámky** — nepovinné a delší. Sem patří všechno, co se na jeden řádek
  popisu nevejde: co se cestou pokazilo, proč se práce protáhla, na koho se
  doptat. Můžete psát na více řádků; zalomení zůstanou zachovaná. Poznámka se
  ukazuje jen v **detailu zakázky** — v tabulkách ani ve stažených souborech
  není.
- **Moje hodiny** — kolik hodin jste na zakázce odpracovali **vy**. Toto pole je
  **povinné**.
- Hodiny se zapisují **po půlhodinách** — `0.5`, `1`, `1.5`, `2` a tak dále.
  Nejmenší zapsatelná hodnota je půl hodiny. Totéž platí pro hodiny
  spolupracovníků i pro motohodiny strojů.

**2. Spolupracovníci**

Do každého řádku vyberte **Pracovníka** a zapište **Hodiny**, které odpracoval
on. Kdo je tu uvedený, uvidí zakázku ve své historii a hodiny se mu započítají.

- Sebe zde neuvádíte — vaše hodiny jsou už v poli *Moje hodiny*.
- Když nikdo další nedělal, nechte řádky prázdné.
- **Každého člověka uveďte jen jednou.** Kdo už je vybraný v jiném řádku, ten
  se v nabídce dalšího řádku neobjeví, a zakázku se stejným člověkem na dvou
  řádcích aplikace neuloží. Odpracoval-li práci na dvakrát, sečtěte hodiny do
  jednoho řádku.
- Nestačí-li vám připravené řádky, klepněte pod nimi na **„+ další řádek"**.
  Stránka se načte znovu s jedním řádkem navíc a **všechno, co jste už vyplnili,
  v ní zůstane**. Nic se tím neukládá a nic se nekontroluje — přidat řádek můžete
  i do rozdělaného formuláře.
- Řádek navíc odeberete tlačítkem **„− odebrat řádek"** vedle něj. Odebere se
  vždy **poslední** řádek té části — i kdyby byl vyplněný, takže se nejdřív
  podívejte, co v něm je. Poslední zbývající řádek odebrat nejde a tlačítko se
  u něj proto vůbec nezobrazí.

**2a. Zapsat za někoho jiného** *(jen vedoucí a správci)*

Vedoucí má nad polem s hodinami výběr **Zapsat za**. Ve výchozím stavu je
nastavený na **Za sebe**. Když vyberete pracovníka, zakázka bude **jeho** —
jeho jméno u ní, jeho hodiny i jeho položky spotřeby a výroby, a uvidí ji
v **Hodinách** mezi svými posledními zápisy. Zakázka je rovnou **schválená**,
protože ji zapisoval vedoucí; jako posuzovatel jste u ní vedený vy.

Do spolupracovníků pak nevybíráte toho, za koho zapisujete (sám se sebou
spolupracovat nelze) — zato tam můžete uvést i sebe, pokud jste na zakázce
dělal také.

Běžný pracovník tento výběr nemá a zapisuje vždy sám za sebe.

**2b. Datum provedení**

V poli **Datum provedení** je předvyplněný **dnešek**. Když zapisujete práci
zpětně, přepište ho na den, kdy se práce skutečně dělala.

- Datum musí být vyplněné — prázdné pole aplikace odmítne, stejně jako datum
  v budoucnosti.
- Zadává se jen **den**, ne čas.
- Opravit datum může později vedoucí v úpravě zakázky.

> Podle data provedení se řídí **všechny přehledy** — **Hodiny**, **Stroje**,
> **Materiál** i **Přehled**, jejich filtry, sloupce *Provedeno* i řazení.
> Zakázka zapsaná dnes za práci z minulého měsíce se tedy započítá do minulého
> měsíce.
> V detailu zakázky najdete obě data: *Datum provedení* i *Zaznamenáno*.

**3. Spotřeba a výroba**

- Do **spotřeby** zapište, co se spotřebovalo (materiál a množství).
- **Množství se všude zadává v tunách** — jiná jednotka v aplikaci není.
- Do **výroby** zapište, co vzniklo.
- Řádků je připraveno několik. Nepoužité nechte prázdné.
- Když jich potřebujete víc — třeba když z jednoho materiálu vzniknou čtyři
  frakce — klepněte pod tou částí na **„+ další řádek"**. Přidá se jeden prázdný
  řádek a vyplněné údaje zůstanou. Tlačítko má každá část formuláře zvlášť.
- **„− odebrat řádek"** vedle něj odebere poslední řádek té části. Prázdné řádky
  ale odebírat nemusíte — aplikace je při ukládání jednoduše přeskočí.
- Vyplnit musíte buď celý řádek, nebo žádný — samotné množství bez materiálu
  aplikace odmítne.
- **Stejný materiál uveďte v jedné části jen jednou.** Materiál vybraný v jiném
  řádku se v nabídce dalšího řádku neobjeví a dva řádky se stejným materiálem
  aplikace odmítne — sečtěte množství do jednoho řádku. Ve **spotřebě** i ve
  **výrobě** zároveň týž materiál být může; to jsou dvě různá sdělení o něm.
- Vyplněná musí být **alespoň jedna položka spotřeby a alespoň jedna položka
  výroby**. Samotná spotřeba bez výroby (ani naopak) se uložit nedá.
- **Celkové množství spotřeby se musí rovnat celkovému množství výroby.**
  Sčítají se všechny řádky dohromady, ne řádek proti řádku — když z 20 t
  kameniva vznikne 14 t frakce 8/16 a 6 t frakce 4/8, souhlasí to.
- Musí to sedět přesně. Když se součty liší, aplikace zakázku neuloží a nahoře
  napíše, kolik jí na které straně vyšlo — například *„Celkové množství spotřeby
  a výroby se musí rovnat (spotřeba 20, výroba 19)."* Dopište chybějící řádek
  nebo opravte množství.
- Neuloží se přitom **nic** — ani hodiny, ani stroje. Po opravě součtů se odešle
  celá zakázka najednou.

**4. Stroje**

Řádek stroje má čtyři pole: **Stroj**, **Hodiny** (motohodiny), **Tuny**, které
stroj zpracoval, a **Natankováno (l)**. Pokud šel materiál přes více strojů za
sebou, použijte více řádků. **Každý stroj uveďte jen jednou** — vybraný stroj se
v nabídce dalšího řádku neobjeví a dva řádky se stejným strojem aplikace odmítne;
běžel-li na zakázce víckrát, sečtěte motohodiny, tuny i litry do jednoho řádku.
Na další řádky se dostanete tlačítkem **„+ další řádek"** pod nimi a přebytečný
odeberete tlačítkem **„− odebrat řádek"**. Stroje jsou nepovinné — pokud se žádný
nepoužil, nechte řádky prázdné.

**Motohodiny a tuny patří k sobě, litry stojí samostatně.** Řádek tedy můžete
vyplnit třemi způsoby:

- **Stroj + hodiny + tuny** — stroj běžel; litry nechte prázdné, pokud se
  netankovalo.
- **Stroj + litry** — jen se tankovalo. Hodiny ani tuny vyplňovat nemusíte.
- **Stroj + hodiny + tuny + litry** — běžel i se tankoval, oboje na jednom řádku.

Co aplikace odmítne: hodiny bez tun (nebo tuny bez hodin), litry bez vybraného
stroje, a stroj, u kterého není vyplněné vůbec nic.

> Tuny u stroje se **nezapočítávají** do kontroly součtů spotřeby a výroby.
> Když materiál projde třemi stroji za sebou, projde každý z nich stejné
> množství — proto se tato čísla nesčítají do celkové výroby. Litry se
> nezapočítávají nikam a z motohodin se nepočítají.

**4b. Jen tankování, bez zpracování**

**Když jste jen tankovali, stačí zapsat stroje a litry — a nic jiného.** Takový
zápis je platná zakázka: nemusíte vyplňovat spotřebu ani výrobu (kontrola součtů
se na něj nevztahuje, protože není co srovnávat), nemusíte vyplnit **Popis** ani
**Moje hodiny**. Stačí **Datum provedení**, které je předvyplněné na dnešek, a
libovolný počet řádků *Stroj + Natankováno (l)*.

- Žádné odpracované hodiny se u takové zakázky nezapíšou.
- **Popis** můžete vyplnit, i když se nevyžaduje — v přehledech se pak zobrazí
  místo pomlčky, takže se lépe hledá.
- Schvaluje se úplně stejně jako každá jiná zakázka a objeví se i v **Přehledu**.
- Jakmile na takový zápis přidáte motohodiny nebo položku materiálu, přestává
  to být „jen tankování" — **Popis**, **Moje hodiny** i kontrola součtů se
  vrátí.

> Celá zakázka se ukládá najednou — položky, hodiny lidí, hodiny strojů i
> tankování. Po uložení se formulář vyprázdní a můžete zapsat další zakázku.

## Schvalování

**Zakázku, kterou zapíše pracovník, musí schválit vedoucí.** Po odeslání se
uloží a aplikace napíše, že *čeká na schválení*. Do té doby se **nezapočítává**
do přehledu **Hodiny** ani do **Strojů** — proto tam své hodiny hned po odeslání
neuvidíte. Jakmile ji vedoucí schválí, objeví se všude.

Zakázka zapsaná vedoucím je schválená rovnou.

Když je něco špatně, opravu provede vedoucí přímo v zakázce — vy ji opravit
nemůžete. Zakázku, která je celá špatně, vedoucí smaže. Zpátky k přepracování se
zakázka neposílá, takže pokud víte o chybě, dejte vedoucímu vědět, jak to bylo
správně.

Jak na tom vaše zakázky jsou, uvidíte v záložce **Hodiny** v tabulce *Moje
poslední zápisy* — viz [Hodiny](#hodiny).

## Přehled zpracování (pro vedoucí)

Tuto sekci mají v menu pouze vedoucí a správci. Je v ní **seznam všech
zapsaných zpracování** — datum, kdo je zapsal, popis, hodiny celkem a stav.
Zakázky, které čekají na schválení, jsou **žlutě** zvýrazněné; nahoře je počet
čekajících. Filtrovat lze podle stavu, pracovníka a data.

Klepnutím na **Detail** otevřete celou zakázku se vším, co bylo ve formuláři
vyplněno — hodiny všech lidí, spotřeba, výroba, stroje s motohodinami a tunami
i **tankování**. Dole jsou akce:

| Akce | Co udělá |
|---|---|
| **Schválit** | Zakázka se započítá do Hodin i Strojů. |
| **Upravit** | Otevře stejný formulář jako Zpracování, předvyplněný. Uložením se řádky přepíšou. **Schválení tím nevzniká** — po opravě je ještě potřeba schválit. |
| **Smazat** | Nevratně smaže celou zakázku i její řádky. Její hodiny, tuny i natankované litry tím ze Strojů a Hodin zmizí. |

Při úpravě platí stejná pravidla jako při zápisu — hlavně že se **součty
spotřeby a výroby musí rovnat**; u zakázky, která je jen tankování, se
nekontrolují, stejně jako při zápisu. Pole *hodiny* patří tomu, kdo zakázku
zapsal, ne vám.

## Stroje

Přehled strojů, jejich **celkových motohodin** a **celkového počtu zpracovaných
tun**. Obě čísla se navyšují pokaždé, když někdo ve **Zpracování** vyplní hodiny
a tuny stroje a vedoucí zakázku **schválí** — neschválené zakázky se do nich
nepočítají.

U starších záznamů, které vznikly ještě předtím, než se tuny zapisovaly, není
tonáž známá. Takové stroje mají ve sloupci **Celkem tun** pomlčku (—) — neplést
s nulou, ta by znamenala, že stroj nic nezpracoval.

Vedle motohodin se zobrazují dvě sazby stroje: **Sazba (Kč/hod)** a
**Cena (Kč/t)** za tunu zpracovaného materiálu. V aplikaci se nezadávají,
nastavuje je správce; nevyplněná sazba se zobrazí jako pomlčka (—).

Z těchto sazeb se počítají tři sloupce s penězi — vždy **za období, které máte
nastavené filtrem**:

| Sloupec | Jak vzniká |
|---|---|
| **Cena za hodiny (Kč)** | motohodiny × **Sazba (Kč/hod)** |
| **Cena za tuny (Kč)** | zpracované tuny × **Cena (Kč/t)** |
| **Celkem (Kč)** | součet toho, čím je stroj naceněný |

**Nevyplněná sazba neznamená zadarmo, ale „takhle se stroj neúčtuje".** Proto
je v takovém sloupci pomlčka (—) a do **Celkem** se nezapočítává: stroj
naceněný jen po hodinách má v **Celkem** právě cenu za hodiny.

Pomlčka je i v **Celkem**, a to ve dvou případech: stroj nemá vyplněnou žádnou
sazbu, nebo je naceněný po tunách, ale u jeho starších záznamů se tonáž
nezapisovala. Cenu tehdy opravdu nelze spočítat a číslo, které by z ní ukázalo
jen polovinu, by bylo horší než pomlčka.

Naopak **0,00** je odpověď: naceněný stroj, který v daném období neběžel, nic
nestál.

Stránka má stejnou stavbu jako **Hodiny**: nahoře **filtr**, pod ním tabulka
**Stav strojů**, potom **Tankování** a **Detail tankování** a úplně dole
**Detail používání strojů** — jednotlivé záznamy (datum, stroj, hodiny, tuny,
zakázka a kdo ji zapsal), ze kterých se čísla nahoře skládají. Filtr platí pro
všechny tabulky najednou, takže si můžete číslo přečíst a hned pod ním vidět,
z čeho vzniklo.

**Tankování** je samostatná tabulka: u každého stroje **počet tankování** a
**natankované litry** za zvolené období, dole celkový součet za všechny stroje.
Pod ní je **Detail tankování** — jednotlivá tankování s datem, strojem, počtem
litrů, zakázkou a tím, kdo ji zapsal.

- Litry se **nepočítají** z motohodin ani z tun; berou se jen z toho, co někdo
  ve **Zpracování** vyplnil do pole *Natankováno (l)*.
- Stejně jako u ostatních čísel se počítají jen **schválené** zakázky.
- **0,00 l** je odpověď: ten stroj se v daném období netankoval. Pomlčka tady
  nikdy není, litry se buď zapsaly, nebo se netankovalo.
- **Detail tankování** a **Detail používání strojů** se stránkují každý zvlášť —
  přechod na další stránku jedné tabulky s druhou nehýbe.

Obě souhrnné tabulky — **Stav strojů** i **Tankování** — mají vlastní tlačítko
**Stáhnout do CSV / Excelu**; stáhne se přesně to, co máte nastavené filtrem.

Filtrovat lze podle stroje, data a podle toho, kdo zakázku zapsal
(**Vytvořil**); filtr se vztahuje i na tabulky s tankováním.
**Bez filtru vidíte všechny stroje.** Když vyberete konkrétní
stroj, zůstane v tabulce jen on; když omezíte datum, stroje zůstanou všechny
a čísla se přepočítají za dané období — stroj s **0 h** tedy znamená, že v tom
období neběžel.

Vidíte vždy záznamy celého provozu, ne jen své vlastní — na tuto stránku se
běžný pracovník nedostane.

> **Rychlé období.** Nad každým filtrem v aplikaci jsou čtyři tlačítka —
> **Vše**, **Posledních 7 dní**, **Posledních 30 dní** a **Minulý měsíc**.
> Klepnutím se datumy vyplní samy, zeleně svítí období, které je právě
> nastavené, a **Vše** filtrování podle data zase zruší. Ostatní filtry
> (pracovník, stroj, materiál, stav) zůstanou zachované.

Tato sekce je jen pro vedoucí — běžný pracovník ji nemá v menu a stránka se mu
neotevře, ani když si její adresu zadá ručně.

## Materiál

Odpověď na otázku **„kolik jsme toho vyrobili?"** — po materiálech a za zvolené
období. Čísla se skládají z řádků, které se vyplňují ve **Zpracování**, takže
nic navíc zapisovat nemusíte.

Stránka má stejnou stavbu jako **Stroje**: nahoře **filtr**, pod ním tabulka
**Souhrn materiálů** a dole **Detail položek** — jednotlivé řádky (datum,
materiál, druh, množství, zakázka a kdo ji zapsal), ze kterých se čísla nahoře
skládají. Filtr platí pro obě tabulky najednou.

V souhrnu jsou u každého materiálu tři čísla:

| Sloupec | Co znamená |
|---|---|
| **Spotřebováno** | Kolik tun toho materiálu se za dané období zpracovalo (vstup) |
| **Vyrobeno** | Kolik tun toho materiálu za dané období vzniklo (výstup) |
| **Rozdíl** | Vyrobeno mínus spotřebováno |

**Rozdíl** má smysl hlavně u materiálu, který je jednou vstupem a podruhé
výstupem: může vyjít i záporný, a to znamená, že se ho víc zpracovalo, než
vyrobilo.

Filtrovat lze podle materiálu a data. **Bez filtru vidíte všechny materiály.**
Když vyberete konkrétní materiál, zůstane v tabulce jen on; když omezíte datum,
materiály zůstanou všechny a čísla se přepočítají za dané období — materiál
s **0,00 t** tedy znamená, že se v tom období nezpracovával. Nabídka filtru
obsahuje i vyřazené materiály, aby se dalo dohledat, co se s nimi dělo dřív;
v souhrnu se vyřazené materiály samy o sobě nezobrazují.

Typický postup pro otázku „kolik jsme vyrobili frakce 8/16 minulý měsíc":
klepněte na **Minulý měsíc**, vyberte materiál a přečtěte sloupec **Vyrobeno**.

> Do čísel se počítají **jen schválené zakázky**, stejně jako v Hodinách
> a Strojích. Zakázka, která čeká na schválení, se tu neobjeví.

Tato sekce je jen pro vedoucí — běžný pracovník ji nemá v menu a stránka se mu
neotevře, ani když si její adresu zadá ručně.

## Hodiny

Přehled odpracovaných hodin lidí — nahoře souhrn po pracovnících, dole seznam
jednotlivých zakázek.

Můžete filtrovat podle data (**Datum od**, **Datum do**) nebo použít tlačítka
rychlého období nad filtrem.

> Každému se počítají **hodiny, které si sám zapsal** — vy své v poli *Moje
> hodiny*, spolupracovníci ty své ve svých řádcích. Nejsou to hodiny strojů:
> motohodiny se sledují zvlášť v sekci **Stroje** a s hodinami lidí nijak
> nesouvisí.

Běžný pracovník vidí pouze své vlastní hodiny. Vedoucí vidí všechny a může
filtrovat podle pracovníka.

### Moje poslední zápisy

Nahoře v záložce **Hodiny** je tabulka **posledních pěti zakázek, které jste
zapsali** — datum provedení, popis, vaše hodiny a stav. Řadí se podle toho, kdy
jste je zapsali (nejnovější nahoře), ne podle data provedení — zpětně zapsaná
zakázka se tak neztratí někde dole, když si chcete zkontrolovat, jestli už ji
vedoucí schválil. Žlutě zvýrazněné a označené *Čeká
na schválení* jsou ty, které vedoucí ještě neposoudil; *Schváleno* znamená, že
se už započítávají do Hodin, Strojů i Materiálu. Je to tedy vysvětlení, proč
vám hodiny v souhrnu pod tabulkou zatím chybí. Zakázka, kterou vedoucí smazal, ze seznamu
zmizí.

Jsou v ní jen zakázky, které jste zapsali vy — ne ty, na kterých vás někdo jen
uvedl jako spolupracovníka; ty se vám po schválení objeví přímo v souhrnu
hodin. Filtry nad souhrnem se této tabulky netýkají, vždy ukazuje vašich pět
posledních zápisů.

Vedoucí tuto tabulku nemá — jeho zakázky jsou schválené rovnou a všechny
ostatní vidí v **Přehledu**.

## Stažení do Excelu

Pod souhrnnou tabulkou v **Hodinách**, **Strojích** i **Materiálu** je tlačítko
**Stáhnout do CSV / Excelu**. Stáhne se soubor s tou tabulkou, která je právě
na obrazovce — **i s nastaveným filtrem**. Postup je tedy vždy stejný: nejdřív
si stránku nafiltrujte (třeba **Minulý měsíc**), zkontrolujte čísla a teprve
pak klepněte na tlačítko.

| Stránka | Co se stáhne |
|---|---|
| **Hodiny** | Tabulka **Souhrn** — pracovník, hodiny, počet zakázek |
| **Stroje** | Tabulka **Stav strojů** — stroj, hodiny, tuny, obě sazby a tři sloupce s cenou |
| **Materiál** | Tabulka **Souhrn materiálů** — spotřebováno, vyrobeno, rozdíl |
| **Materiál** | Tabulka **Detail položek** — datum, materiál, druh, množství, zakázka, kdo ji zapsal |

Na **Materiálu** jsou tedy tlačítka dvě, každé pod svou tabulkou. To druhé,
pod **Detailem položek**, stáhne jednotlivé řádky — co která zakázka
spotřebovala a co vyrobila, řádek po řádku. Hodí se, když si měsíc potřebujete
odsouhlasit s dodacími listy; souhrn na to nestačí, protože v něm je z každého
materiálu jen jedno číslo.

Do tohoto souboru se stáhnou **všechny řádky, které filtr pustí** — ne jenom ta
stránka, kterou máte zrovna otevřenou. Pod tabulkou se čísluje po padesáti
řádcích, v souboru je jich tolik, kolik jich filtru odpovídá.

Na ostatních stránkách se stahuje **souhrn**, ne dlouhý seznam jednotlivých
záznamů pod ním.

Soubor se jmenuje například `souhrn-hodin-2026-09-06.csv` — datum ke konci je
den, kdy jste ho stáhli, aby se dva soubory ve stažených nepřepsaly. Otevřete
ho poklepáním; Excel ho rozdělí do sloupců sám a čísla v něm umí sečíst.
Prázdná buňka ve sloupci **Tun**, u sazby nebo v některém ze sloupců s cenou
znamená **není známo** (na stránce je na jejím místě pomlčka) — nulu tam
schválně nepíšeme, aby vám nepokazila součet. V detailu položek je ze stejného
důvodu prázdný i sloupec **Zakázka** u starších zakázek, u kterých nikdo
nevyplnil popis — dnes už je popis povinný, takže nové zakázky ho mají vždy.

Do souboru se dostane přesně to, co je na stránce: **jen schválené zakázky**,
a pracovník si stáhne jenom své vlastní hodiny. Když je filtr špatně vyplněný
(stránka to hlásí červeně), stáhne se soubor jen se záhlavím a bez řádků —
stejně jako je tabulka na stránce prázdná.

## Časté potíže

**„Zadejte číslo."**
Použili jste čárku. Desetinná místa pište s tečkou — `12.5`.

**„Zajistěte, aby tato hodnota byla 0.5 násobkem velikosti kroku…"**
U hodin jste zapsali jinou hodnotu než celou nebo půlhodinu — třeba `1.25`.
Zaokrouhlete na nejbližší půlhodinu.

**„Toto pole je vyžadováno."**
Některé povinné pole zůstalo prázdné — nejčastěji *Moje hodiny*.

**„Vyplňte materiál i množství, nebo řádek nechte prázdný."**
Ve **Zpracování** je rozepsaný řádek. Buď ho doplňte celý, nebo vymažte. Stejně
to platí pro řádky spolupracovníků a strojů.

**„Přidejte alespoň jednu položku spotřeby a jednu položku výroby."**
Zakázka musí mít obě strany — co se spotřebovalo i co vzniklo. Jednostranný
záznam aplikace neuloží.

**„Celkové množství spotřeby a výroby se musí rovnat."**
Součet množství na straně spotřeby nesedí se součtem na straně výroby; v závorce
hlášky jsou obě čísla. Sčítají se všechny řádky dohromady, takže chybí buď
řádek, nebo je někde překlep v množství. Dokud to nesedí, neuloží se ani hodiny
a stroje.

**Nevidím v menu Stroje, Materiál ani Přehled.**
Jsou jen pro vedoucí. Pokud je potřebujete, řekněte si správci o změnu role.

**Zapsal jsem zakázku, ale v Hodinách ji nevidím.**
Čeká na schválení vedoucím. Po schválení se objeví v Hodinách, ve Strojích
i v Materiálu.

**Nevidím záznamy kolegů.**
Běžný pracovník vidí své záznamy a zakázky, kde je uvedený jako spolupracovník.
Je to záměr. Kdo potřebuje vidět vše, potřebuje roli vedoucího.

**V nabídce chybí materiál nebo stroj.**
Nejspíš byl vyřazen. Požádejte správce, aby ho v administraci znovu označil jako
aktivní.

**Zapomenuté heslo.**
Aplikace neumí obnovu hesla přes e-mail. Požádejte správce o nastavení nového.

**Špatně zadaná zakázka.**
Jako pracovník ji opravit nemůžete. Řekněte vedoucímu — ten ji v **Přehledu**
opraví, nebo vám ji vrátí s poznámkou, případně smaže.
