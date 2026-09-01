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

- Do **spotřeby** zapište, co se spotřebovalo (materiál, lokalita, množství).
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

Vyplňte **Stroj** a počet **Hodin** (motohodin). Pokud šel materiál přes více
strojů za sebou, použijte více řádků. Stejný stroj můžete uvést vícekrát.
Stroje jsou nepovinné — pokud se žádný nepoužil, nechte řádky prázdné.

> Celá zakázka se ukládá najednou — položky, hodiny lidí i hodiny strojů. Po
> uložení se formulář vyprázdní a můžete zapsat další zakázku.

## Stroje

Přehled strojů a jejich **celkových motohodin**. Číslo se navyšuje automaticky
pokaždé, když někdo ve **Zpracování** vyplní hodiny stroje.

Vedle motohodin se zobrazují dvě sazby stroje: **Sazba (Kč/hod)** a
**Cena (Kč/t)** za tunu zpracovaného materiálu. Obě jsou pouze orientační —
nic se z nich nepočítá a v aplikaci se nezadávají, nastavuje je správce.
Nevyplněná sazba se zobrazí jako pomlčka (—).

Klepnutím na název stroje zobrazíte jeho historii použití. Tu lze filtrovat
podle stroje, data a — u vedoucích — podle pracovníka.

Tuto sekci mají v menu pouze vedoucí.

## Hodiny

Přehled odpracovaných hodin lidí — nahoře souhrn po pracovnících, dole seznam
jednotlivých zakázek.

Můžete filtrovat podle data (**Datum od**, **Datum do**).

> Každému se počítají **hodiny, které si sám zapsal** — vy své v poli *Moje
> hodiny*, spolupracovníci ty své ve svých řádcích. Nejsou to hodiny strojů:
> motohodiny se sledují zvlášť v sekci **Stroje** a s hodinami lidí nijak
> nesouvisí.

Běžný pracovník vidí pouze své vlastní hodiny. Vedoucí vidí všechny a může
filtrovat podle pracovníka.

## Časté potíže

**„Zadejte číslo."**
Použili jste čárku. Desetinná místa pište s tečkou — `12.5`.

**„Zajistěte, aby tato hodnota byla 0.5 násobkem velikosti kroku…"**
U hodin jste zapsali jinou hodnotu než celou nebo půlhodinu — třeba `1.25`.
Zaokrouhlete na nejbližší půlhodinu.

**„Toto pole je vyžadováno."**
Některé povinné pole zůstalo prázdné — nejčastěji *Moje hodiny*.

**„Vyplňte materiál, lokalitu a množství, nebo řádek nechte prázdný."**
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

**Nevidím v menu Stroje.**
Je jen pro vedoucí. Pokud je potřebujete, řekněte si správci o změnu role.

**Nevidím záznamy kolegů.**
Běžný pracovník vidí své záznamy a zakázky, kde je uvedený jako spolupracovník.
Je to záměr. Kdo potřebuje vidět vše, potřebuje roli vedoucího.

**V nabídce chybí materiál nebo stroj.**
Nejspíš byl vyřazen. Požádejte správce, aby ho v administraci znovu označil jako
aktivní.

**Zapomenuté heslo.**
Aplikace neumí obnovu hesla přes e-mail. Požádejte správce o nastavení nového.

**Špatně zadaná zakázka.**
Ve formuláři se opravit nedá — záznamy se nemažou. Opravu provede správce
v administraci.
