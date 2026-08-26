# Uživatelská příručka

Evidence materiálu — návod pro obsluhu skladu.

Aplikace je určená především pro mobilní telefon. Otevřete ji v prohlížeči na
adrese, kterou vám dal správce, a přihlaste se uživatelským jménem a heslem.

---

## Obsah

- [Přihlášení a změna hesla](#přihlášení-a-změna-hesla)
- [Spodní menu](#spodní-menu)
- [Sklad](#sklad)
- [Příjem](#příjem)
- [Výdej](#výdej)
- [Zpracování](#zpracování)
- [Stroje](#stroje)
- [Hodiny](#hodiny)
- [Úprava](#úprava-pouze-vedoucí)
- [Historie a export do Excelu](#historie-a-export-do-excelu)
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
| **Sklad** | Kolik čeho je momentálně na kterém místě |
| **Příjem** | Materiál přišel na sklad |
| **Výdej** | Materiál odešel ze skladu |
| **Zpracování** | Z jednoho materiálu vznikl jiný |
| **Stroje** | Celkové odpracované hodiny strojů |
| **Hodiny** | Odpracované hodiny lidí |
| **Úprava** | Ruční oprava stavu skladu — vidí jen vedoucí |

## Sklad

Přehled toho, co je právě teď na skladě: materiál, lokalita a množství.

- Řádky s **červeným záporným číslem** znamenají, že je evidováno více vydaného
  materiálu než přijatého. Většinou to znamená, že se zapomněl zadat příjem.
  Nahlaste to vedoucímu.
- Klepnutím na řádek zobrazíte historii pohybů právě tohoto materiálu na této
  lokalitě.
- Odkaz **Zobrazit celou historii →** otevře historii všech pohybů.

Materiály, kterých je na skladě přesně nula, se v přehledu nezobrazují.

## Příjem

Použijte, když materiál **přijde na sklad**.

1. Vyberte **Materiál** a **Lokalitu**, kam se ukládá.
2. Zadejte **Množství**.
3. Do **Poznámky** můžete napsat například číslo dodacího listu nebo dodavatele.
   Poznámka není povinná, ale hodí se při pozdějším dohledávání.
4. Zapisujete-li příjem až později, zaškrtněte **Jiné datum a čas než teď** a
   vyplňte, kdy materiál doopravdy přišel. Bez zaškrtnutí se uloží aktuální čas
   — políčko s datem se bez něj nepoužije, i kdybyste do něj něco napsali.
5. Klepněte na tlačítko pro uložení.

Po uložení vás aplikace vrátí na **Sklad** a nahoře se zobrazí potvrzení
*Příjem byl zaznamenán.*

> Vyplněné datum a čas se použijí všude, kde se pohyb zobrazuje — v **Historii**,
> ve filtrech i v exportu do Excelu. Nelze zadat čas v budoucnosti.

> **Množství pište s tečkou, ne s čárkou** — tedy `12.5`, nikoli `12,5`.
> Při čárce se zobrazí chyba *Zadejte číslo.*

## Výdej

Použijte, když materiál **odchází ze skladu**.

Postup je stejný jako u příjmu, včetně zaškrtávátka **Jiné datum a čas než teď**
pro výdej zapsaný se zpožděním. Rozdíl je v kontrole:

> Pokud na dané lokalitě není dostatek materiálu, výdej se **neuloží** a zobrazí
> se hláška *K dispozici je pouze … ; výdej byl zrušen.* Zkontrolujte, zda jste
> vybrali správnou lokalitu a zda nechybí dřívější příjem.

U některých materiálů — například zeminy vytěžené přímo na místě — se množství
na skladě nesleduje a tato kontrola se neprovádí. Nastavuje to správce.

## Zpracování

Použijte, když se **z jednoho materiálu stane jiný** — drcení, třídění, řezání.

Formulář má tři části:

**1. Popis a spolupracovníci**

- **Popis** — krátce, o jakou práci šlo. Například *Drcení kameniva na frakci 8/16*.
- **Spolupracovníci** — zaškrtněte ostatní, kdo na zakázce dělali. Uvidí ji pak
  ve své historii a započítají se jim hodiny. Sebe nezaškrtáváte, jste uvedeni
  automaticky jako zadavatel.

**2. Spotřeba a výroba**

- Do **spotřeby** zapište, co se spotřebovalo (materiál, lokalita, množství).
- Do **výroby** zapište, co vzniklo.
- Řádků je připraveno několik. Nepoužité nechte prázdné.
- Vyplnit musíte buď celý řádek, nebo žádný — samotné množství bez materiálu
  aplikace odmítne.
- Vyplněná musí být alespoň jedna položka spotřeby nebo výroby.

**3. Stroje**

Vyplňte **Stroj** a počet **Hodin**. Pokud šel materiál přes více strojů za
sebou, použijte více řádků. Stejný stroj můžete uvést vícekrát. Stroje jsou
nepovinné — pokud se žádný nepoužil, nechte řádky prázdné.

> Celá zakázka se ukládá najednou. Pokud na některý spotřebovaný materiál
> nestačí zásoba, **neuloží se nic** — ani hodiny strojů. Aplikace vypíše
> všechny chybějící položky naráz, takže je můžete opravit v jednom kroku.

## Stroje

Přehled strojů a jejich **celkových odpracovaných hodin**. Číslo se navyšuje
automaticky pokaždé, když někdo v **Zpracování** vyplní hodiny stroje.

Klepnutím na název stroje zobrazíte jeho historii použití.

## Hodiny

Přehled odpracovaných hodin lidí — nahoře souhrn po pracovnících, dole seznam
jednotlivých zakázek.

Můžete filtrovat podle data (**Datum od**, **Datum do**).

> **Důležité:** pokud na zakázce dělali dva lidé, započítá se **oběma celá doba
> zakázky**, ne polovina. Tříhodinová práce dvou lidí je u každého z nich
> 3 hodiny. Součet hodin lidí proto může být vyšší než součet hodin strojů —
> je to záměr, sleduje se odvedená práce, ne chod stroje.

Běžný pracovník vidí pouze své vlastní hodiny. Vedoucí vidí všechny a může
filtrovat podle pracovníka.

## Úprava (pouze vedoucí)

Ruční oprava stavu skladu — po inventuře, při zjištěné ztrátě nebo pro srovnání
nesrovnalosti. Běžní pracovníci tuto volbu v menu nemají.

1. Vyberte **Materiál** a **Lokalitu**.
2. Zvolte **Směr** — *Navýšit sklad* nebo *Snížit sklad*.
3. Zadejte **Množství**.
4. Vyplňte **Poznámku** — zde je **povinná**. Uveďte důvod úpravy, například
   *Inventura 8/2026*. Poznámka zůstává natrvalo v historii.

Snížit sklad nelze pod nulu.

## Historie a export do Excelu

Historii otevřete ze **Skladu** — buď klepnutím na konkrétní řádek, nebo
odkazem *Zobrazit celou historii →*.

Je vidět datum, materiál, lokalita, typ pohybu, množství, kdo záznam pořídil
a poznámka. Záporná čísla jsou výdeje a spotřeba.

Filtrovat lze podle materiálu, lokality, typu pohybu, data a — u vedoucích —
podle pracovníka. Odkaz **Exportovat CSV** stáhne právě to, co je vyfiltrované,
ne celou historii.

> Stažený soubor otevřete v Excelu poklepáním. Je připravený pro české
> nastavení Excelu — sloupce se rozdělí samy, čísla a data jsou rozpoznaná jako
> čísla a data a diakritika je v pořádku.

Běžný pracovník vidí a exportuje pouze své vlastní záznamy a zakázky, na kterých
byl uveden jako spolupracovník. Vedoucí vidí vše.

## Časté potíže

**„Zadejte číslo."**
Použili jste čárku. Desetinná místa pište s tečkou — `12.5`.

**„Toto pole je vyžadováno."**
Některé povinné pole zůstalo prázdné. U úpravy skladu je povinná i poznámka.

**„K dispozici je pouze … ; výdej byl zrušen."**
Na skladě není dost materiálu. Zkontrolujte lokalitu — materiál může být na
jiné. Pokud lokalita sedí, nejspíš chybí dřívější příjem; ohlaste to vedoucímu,
který ho doplní přes **Úpravu**.

**„Vyplňte materiál, lokalitu a množství, nebo řádek nechte prázdný."**
V **Zpracování** je rozepsaný řádek. Buď ho doplňte celý, nebo vymažte.

**Nevidím v menu Úpravu.**
Je jen pro vedoucí. Pokud ji potřebujete, řekněte si správci o změnu role.

**Nevidím záznamy kolegů.**
Běžný pracovník vidí své záznamy a zakázky, kde je uvedený jako spolupracovník.
Je to záměr. Kdo potřebuje vidět vše, potřebuje roli vedoucího.

**Zapomenuté heslo.**
Aplikace neumí obnovu heslem přes e-mail. Požádejte správce o nastavení nového.

**Špatně zadaný pohyb.**
Záznamy nelze mazat ani upravovat — historie musí zůstat úplná. Opravu provede
vedoucí protipohybem přes **Úpravu** s vysvětlující poznámkou.
