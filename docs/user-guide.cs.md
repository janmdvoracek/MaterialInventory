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
- [Hodiny](#hodiny)
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
| **Stroje** | Celkové motohodiny a sazby strojů — vidí jen vedoucí |

Po přihlášení se rovnou otevře **Zpracování**. Stejně tak se tam vrátíte
klepnutím na logo vlevo nahoře.

## Zpracování

Použijte, když se **z jednoho materiálu stane jiný** — drcení, třídění, řezání.

Formulář má čtyři části:

**1. Popis a vaše hodiny**

- **Popis** — krátce, o jakou práci šlo. Například *Drcení kameniva na frakci 8/16*.
  Je nepovinný.
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
- Když stejného člověka uvedete dvakrát, hodiny se mu sečtou.

**3. Spotřeba a výroba**

- Do **spotřeby** zapište, co se spotřebovalo (materiál a množství).
- Do **výroby** zapište, co vzniklo.
- Řádků je připraveno několik. Nepoužité nechte prázdné.
- Vyplnit musíte buď celý řádek, nebo žádný — samotné množství bez materiálu
  aplikace odmítne.
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

Vyplňte **Stroj**, počet **Hodin** (motohodin) a **Tuny**, které stroj
zpracoval. Pokud šel materiál přes více strojů za sebou, použijte více řádků.
Stejný stroj můžete uvést vícekrát. Stroje jsou nepovinné — pokud se žádný
nepoužil, nechte řádky prázdné. Jakmile ale řádek začnete vyplňovat, musí být
vyplněný celý.

> Tuny u stroje se **nezapočítávají** do kontroly součtů spotřeby a výroby.
> Když materiál projde třemi stroji za sebou, projde každý z nich stejné
> množství — proto se tato čísla nesčítají do celkové výroby.

> Celá zakázka se ukládá najednou — položky, hodiny lidí i hodiny strojů. Po
> uložení se formulář vyprázdní a můžete zapsat další zakázku.

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
vyplněno — hodiny všech lidí, spotřeba, výroba i stroje s motohodinami a tunami.
Dole jsou akce:

| Akce | Co udělá |
|---|---|
| **Schválit** | Zakázka se započítá do Hodin i Strojů. |
| **Upravit** | Otevře stejný formulář jako Zpracování, předvyplněný. Uložením se řádky přepíšou. **Schválení tím nevzniká** — po opravě je ještě potřeba schválit. |
| **Smazat** | Nevratně smaže celou zakázku i její řádky; motohodiny se strojům odečtou. |

Při úpravě platí stejná pravidla jako při zápisu — hlavně že se **součty
spotřeby a výroby musí rovnat**. Pole *hodiny* patří tomu, kdo zakázku zapsal,
ne vám.

## Stroje

Přehled strojů a jejich **celkových motohodin**. Číslo se navyšuje pokaždé, když
někdo ve **Zpracování** vyplní hodiny stroje a vedoucí zakázku **schválí** —
neschválené zakázky se do něj nepočítají.

Vedle motohodin se zobrazují dvě sazby stroje: **Sazba (Kč/hod)** a
**Cena (Kč/t)** za tunu zpracovaného materiálu. Obě jsou pouze orientační —
nic se z nich nepočítá a v aplikaci se nezadávají, nastavuje je správce.
Nevyplněná sazba se zobrazí jako pomlčka (—).

Klepnutím na název stroje zobrazíte jeho historii použití. Tu lze filtrovat
podle stroje, data a — u vedoucích — podle pracovníka.

> **Rychlé období.** Nad každým filtrem v aplikaci jsou čtyři tlačítka —
> **Vše**, **Posledních 7 dní**, **Posledních 30 dní** a **Minulý měsíc**.
> Klepnutím se datumy vyplní samy, zeleně svítí období, které je právě
> nastavené, a **Vše** filtrování podle data zase zruší. Ostatní filtry
> (pracovník, stroj, stav) zůstanou zachované.

Tuto sekci mají v menu pouze vedoucí.

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
zapsali** — datum, popis, vaše hodiny a stav. Žlutě zvýrazněné a označené *Čeká
na schválení* jsou ty, které vedoucí ještě neposoudil; *Schváleno* znamená, že
se už započítávají do Hodin i Strojů. Je to tedy vysvětlení, proč vám hodiny
v souhrnu pod tabulkou zatím chybí. Zakázka, kterou vedoucí smazal, ze seznamu
zmizí.

Jsou v ní jen zakázky, které jste zapsali vy — ne ty, na kterých vás někdo jen
uvedl jako spolupracovníka; ty se vám po schválení objeví přímo v souhrnu
hodin. Filtry nad souhrnem se této tabulky netýkají, vždy ukazuje vašich pět
posledních zápisů.

Vedoucí tuto tabulku nemá — jeho zakázky jsou schválené rovnou a všechny
ostatní vidí v **Přehledu**.

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

**Nevidím v menu Stroje ani Přehled.**
Jsou jen pro vedoucí. Pokud je potřebujete, řekněte si správci o změnu role.

**Zapsal jsem zakázku, ale v Hodinách ji nevidím.**
Čeká na schválení vedoucím. Po schválení se v Hodinách i ve Strojích objeví.

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
