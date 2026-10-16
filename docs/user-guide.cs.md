# Uživatelská příručka

Evidence zpracování materiálu. Aplikace je určená hlavně pro mobilní telefon: otevřete ji v prohlížeči na adrese od správce a přihlaste se.

> Aplikace **nevede stav skladu**. Zapisují se zakázky (co se zpracovalo, kdo na tom dělal a jak běžely stroje) a zvlášť tankování strojů. Jestli je materiálu dost, aplikace nehlídá.

## Obsah

- [Přihlášení a heslo](#přihlášení-a-heslo)
- [Aplikace v telefonu](#aplikace-v-telefonu)
- [Spodní menu](#spodní-menu)
- [Zpracování](#zpracování)
- [Tankování](#tankování)
- [Schvalování](#schvalování)
- [Přehled (pro vedoucí)](#přehled-pro-vedoucí)
- [Stroje](#stroje)
- [Materiál](#materiál)
- [Souhrn](#souhrn)
- [Stažení do Excelu](#stažení-do-excelu)
- [Časté potíže](#časté-potíže)

## Přihlášení a heslo

Heslo od správce je dočasné, **po prvním přihlášení si ho změňte**: vpravo nahoře **Změnit heslo**. Odhlásíte se tamtéž odkazem **Odhlásit se**.

Po pěti špatných pokusech se účet na 30 minut zablokuje. Zapomenuté heslo vám nastaví správce, obnova přes e-mail není.

## Aplikace v telefonu

Aplikaci si můžete dát na plochu jako **VyrobaPK**. Otevře se jednou ikonou přes celou obrazovku. Je to stále stejný web se stejným přihlášením.

- **Android (Chrome):** nabídka **⋮** → **Nainstalovat aplikaci** (nebo **Přidat na plochu**).
- **iPhone (Safari):** tlačítko **Sdílet** → **Přidat na plochu**. Při prvním spuštění z plochy se přihlaste znovu.

Bez signálu ukáže aplikace stránku **Bez připojení**. Zakázku bez připojení odeslat nejde: počkejte na signál a klepněte na **Zkusit znovu**.

## Spodní menu

| Tlačítko | K čemu | Kdo vidí |
|---|---|---|
| **Zpracování** | zápis zakázky (otevře se po přihlášení i po klepnutí na logo) | všichni |
| **Tankování** | zápis tankování | všichni |
| **Souhrn** | odpracované hodiny; pracovník navíc tuny, hodiny a tankování strojů | všichni |
| **Přehled** | všechny zakázky a tankování, schvalování | vedoucí |
| **Stroje** | motohodiny, tuny, ceny, tankování | vedoucí |
| **Materiál** | spotřeba a výroba po materiálech | vedoucí |

## Zpracování

Použijte, když se **z jednoho materiálu stane jiný** (drcení, třídění, řezání). Tankování se sem nepíše, má [vlastní formulář](#tankování).

### Základní údaje

- **Lokace**: kde se pracovalo. Povinné. Chybí-li vaše místo v seznamu, řekněte vedoucímu.
- **Popis**: krátce, o jakou práci šlo, nejvýš 40 znaků (např. *Drcení na frakci 8/16*). Povinné, podle popisu se zakázka pozná ve všech přehledech.
- **Poznámky**: nepovinné, libovolně dlouhé. Vidět jsou jen v detailu zakázky.
- **Moje hodiny**: kolik hodin jste odpracovali **vy**. Povinné.
- **Datum provedení**: předvyplněný je dnešek. Když zapisujete zpětně, přepište ho. Nesmí být v budoucnosti. Podle tohoto data se zakázka počítá ve všech přehledech.

Hodiny se píšou **po půlhodinách** (`0.5`, `1`, `1.5`…), nejvýš 24. Desetinná místa pište **s tečkou**.

### Spolupracovníci

V každém řádku vyberte **Pracovníka** a zapište jeho **Hodiny**. Sebe sem nepište. Kdo tu je, tomu se hodiny po schválení započítají. Každého uveďte **jen jednou**: kdo je vybraný v jednom řádku, v dalším se nenabídne.

### Spotřeba a výroba

- Do **Spotřebováno** zapište, co se zpracovalo, do **Vyrobeno**, co vzniklo. **Vše v tunách.**
- Řádek vyplňte celý (materiál i množství), nebo ho nechte prázdný. Prázdné řádky se přeskočí.
- V jedné části uveďte každý materiál **jen jednou**. Ve spotřebě i výrobě zároveň být může.
- Musí být **aspoň jeden řádek spotřeby a aspoň jeden řádek výroby**.
- **Součet spotřeby se musí přesně rovnat součtu výroby.** Sčítá se dohromady: 20 t kamene → 14 t frakce 8/16 + 6 t frakce 4/8 sedí. Když nesedí, aplikace nic neuloží (ani hodiny, ani stroje) a napíše oba součty.

### Stroje

Řádek má tři pole: **Stroj**, **Stav motohodin** (mth) a **Tuny** (t).

- Do motohodin pište **stav počítadla** stroje (např. 12345), ne kolik hodin běžel. To si aplikace spočítá sama.
- **Povinný je jen stroj.** Motohodiny i tuny vyplňte, pokud je znáte. Napsaná **0** znamená totéž co prázdné pole.
- Číslo bez vybraného stroje aplikace odmítne.
- Každý stroj uveďte **jen jednou**. Běžel-li víckrát, sečtěte tuny a u motohodin napište poslední stav.
- **Tuny se vyplní samy** součtem řádků Spotřebováno, u každého řádku s vybraným strojem, a mění se s ním. Když stroj zpracoval jiné množství, číslo přepište; co napíšete sami, už se nemění.
- Tuny u strojů se do kontroly součtů **nepočítají**: když materiál projde třemi stroji, projde každým stejné množství.

### Řádky navíc

Pod každou částí je **„+ další řádek"** a **„− odebrat řádek"**. Vyplněné údaje zůstanou a nic se tím neukládá. Odebírá se vždy **poslední** řádek té části, i když je vyplněný. Poslední zbývající řádek odebrat nejde.

### Součet výstupů (třídění)

Při třídění jde dovnitř jeden materiál a ven několik frakcí. Zvážené máte jen to, co vyšlo. Když má **Spotřebováno jediný řádek**, je pod ním tlačítko **„= součet výstupů"**. To do množství toho řádku zapíše **součet všech řádků Vyrobeno**. Vyplňte proto nejdřív výstupy, pak klepněte na „= součet výstupů". Co v poli bylo předtím, se přepíše.

- Tlačítko nic neukládá. Zakázku pak uložte jako obvykle.
- Máte-li ve Spotřebováno víc řádků, tlačítko se nezobrazí.

### Fotky

Nepovinné. Klepněte na **Přidat fotku** a zvolte **Vyfotit** (fotoaparát) nebo **Vybrat z galerie** (i více najednou). Každá další fotka se **přidá** do seznamu, nic nenahradí. **Odebrat** ji ze seznamu vyřadí.

- Nejvýš **10 fotek** na zakázku, jedna nejvýš **10 MB**, všechny najednou nejvýš **20 MB**.
- Formáty `jpg`, `png`, `webp`, `heic`.
- Aplikace fotky zmenší a uloží jako `jpg`. Dodací list zůstane čitelný.
- **Pole si fotky nepamatuje.** Když formulář něco vytkne, nebo přidáte řádek bez skriptu, vyberte fotky znovu.
- Fotky vidí jen vedoucí v detailu zakázky. Vy se k nim zpátky nedostanete, takže je z telefonu nemažte, dokud je potřebujete.

Vidíte-li místo tlačítka dvě pole (**Vyfoťte fotku** a **Vyberte fotky**), prohlížeč nespustil skript stránky. Funguje to podobně, jen každé pole drží jediný výběr.

### Zapsat za někoho jiného (vedoucí)

Vedoucí má pole **Zapsat za**. Když vyberete pracovníka, zakázka bude **jeho**: jeho jméno, jeho hodiny, objeví se mu v Souhrnu. Je rovnou schválená, posuzovatelem jste vy. Mezi spolupracovníky pak můžete uvést i sebe.

Celá zakázka se ukládá najednou. Po uložení se ukáže zelená zpráva a formulář se vyprázdní.

## Tankování

Použijte, kdykoli jste natankovali jeden nebo více strojů. Je to samostatný zápis, se zakázkou nijak nesouvisí.

- **Poznámky**: nepovinné.
- **Datum provedení**: předvyplněný je dnešek, nesmí být v budoucnosti.
- **Tankované stroje**: v každém řádku vyberte **Stroj** a zapište **litry**. **Zdroj** (odkud nafta je, např. nádrž nebo čerpací stanice) je nepovinný. Řádek bez stroje nebo bez litrů aplikace odmítne, prázdný přeskočí. Musí být aspoň jeden.
- Každý stroj uveďte **jen jednou**. Z jednoho zdroje můžete natankovat víc strojů. Tankoval-li jeden stroj ze dvou zdrojů, zapište to jako dvě tankování.
- Litry pište **s tečkou** (`45.5`), nejvýš 2000. Řádky přidáte a odeberete stejně jako u zakázky.
- Chybí-li zdroj v seznamu, řekněte vedoucímu, přidá ho správce.

Pod formulářem jsou **Moje poslední tankování**: pět posledních, se stavem. **Tankování pracovníka musí schválit vedoucí**, do té doby se nepočítá do Souhrnu ani Strojů. Tankování vedoucího je schválené rovnou.

## Schvalování

**Zakázku i tankování pracovníka musí schválit vedoucí.** Do té doby se nepočítají do Souhrnu, Strojů ani Materiálu, proto tam své hodiny hned neuvidíte. Zápis vedoucího je schválený rovnou.

Chybu opraví vedoucí přímo v zápisu, nebo ho smaže. Vy ho opravit nemůžete a zpátky se neposílá. Víte-li o chybě, řekněte vedoucímu, jak to bylo. Stav svých zakázek uvidíte v [Souhrnu](#souhrn), stav tankování pod formulářem [Tankování](#tankování).

## Přehled (pro vedoucí)

Seznam všech zakázek: datum, autor, lokace, popis, hodiny a stav. Čekající jsou **žluté** a nahoře je jejich počet. Filtrovat lze podle autora, lokace, stavu a data.

**Detail** ukáže vše z formuláře včetně fotek (odkazy **Fotka 1**, **Fotka 2**…). Na telefonu se fotka otevře uvnitř aplikace; mezi fotkami listujete tlačítky **Předchozí fotka** a **Další fotka** a odkazem **Zpět na zpracování** se vrátíte do detailu. Na počítači jsou fotky v detailu i v úpravě připnuté vpravo a zůstávají na místě, když stránkou posouváte, takže údaje porovnáte s fotkou vedle sebe. Klepnutím na fotku ji zobrazíte v plné velikosti, **Zavřít** ji vrátí. Akce:

| Akce | Co udělá |
|---|---|
| **Schválit** | Zakázka se začne počítat do přehledů. |
| **Upravit** | Stejný formulář, předvyplněný. Platí stejná pravidla (součty). **Úprava neschvaluje**, po opravě je třeba schválit. Pole s hodinami patří autorovi zakázky. Nové fotky se přidají. Uloženou fotku odstraníte zaškrtnutím **Odebrat**, a to nevratně. |
| **Smazat** | Nevratně smaže zakázku se vším, co k ní patří. |

### Přehled tankování

Odkaz **Přehled tankování** je pod nadpisem Přehledu, u něj počet tankování, která čekají na schválení. Seznam ukazuje datum, autora, stroje, litry a stav; filtrovat lze podle autora, stavu a data. **Detail** tankování nabízí stejné akce jako zakázka: **Schválit**, **Upravit** (neschvaluje) a **Smazat**. U tankování zapsaných před zavedením zdrojů je zdroj prázdný.

## Stroje

Tabulky pod jedním filtrem (stroj, autor, datum), počítají se jen **schválené** zakázky a tankování:

- **Stav strojů**: souhrn po strojích.
- **Tankování** a **Detail tankování**: litry a jednotlivá tankování, se zdrojem a odkazem na celý zápis.
- **Detail používání strojů**: jednotlivé záznamy.

Bez filtru jsou vidět všechny stroje. Stroj s **0 h** v daném období neběžel.

**Motohodiny jsou stav počítadla**, proto jsou dvě čísla:

- **Stav motohodin**: nejvyšší stav v období, tj. kde počítadlo stálo na konci.
- **Hodin za období**: kolik stroj v období odběhl. V detailu je u každé zakázky **Hodin na zakázce**: stav minus předchozí stav téhož stroje. Předchozí stav se hledá podle hodnoty, ne data, takže na pořadí zápisu nezáleží. U prvního stavu stroje je pomlčka.

**Pomlčka (—) znamená „nevíme", ne nulu.** Nikdo nevyplnil motohodiny nebo tuny, nebo chybí sazba. **0** je skutečná nula.

**Sazby** (Kč/hod a Kč/t) nastavuje správce. Z nich jsou tři sloupce za filtrované období:

| Sloupec | Výpočet |
|---|---|
| **Cena za hodiny** | Hodin za období × Kč/hod |
| **Cena za tuny** | tuny × Kč/t |
| **Celkem** | součet toho, čím je stroj naceněný |

Nevyplněná sazba znamená „takhle se stroj neúčtuje", ne „zdarma". **Celkem** je pomlčka, když se cena spočítat nedá: stroj nemá sazbu, nebo je naceněný po tunách a tuny chybí. Naceněný stroj, který neběžel, má **0,00**.

**Tankování** se bere z formuláře [Tankování](#tankování). Tady pomlčka není, **0,00 l** znamená, že se netankovalo. Obě detailní tabulky se stránkují zvlášť.

## Materiál

Odpověď na „kolik jsme toho vyrobili". Počítá se z řádků ve Zpracování, jen ze **schválených** zakázek.

| Sloupec | Význam |
|---|---|
| **Spotřebováno** | tun zpracovaných (vstup) |
| **Vyrobeno** | tun vzniklých (výstup) |
| **Rozdíl** | vyrobeno minus spotřebováno; záporný = víc se zpracovalo, než vyrobilo |

Filtr: materiál, druh pohybu, lokace, datum. Nabízí i vyřazené materiály, kvůli jejich historii. Pod souhrnem je **Detail položek**, jednotlivé řádky zakázek. **Druh pohybu** (Spotřeba, nebo Výroba) zúží jen Detail položek a jeho export; souhrn má oba směry ve vlastních sloupcích, a tak se nemění. Sloupec **ID** je číslo zakázky (klepnutím ji otevřete); podle něj v exportu poznáte řádky stejné zakázky i dvě jinak stejné zakázky od sebe.

Příklad: „kolik frakce 8/16 minulý měsíc?" → **Minulý měsíc**, vyberte materiál, čtěte **Vyrobeno**.

## Souhrn

**Odpracované hodiny** po lidech a pod nimi **Detail zakázek**. Počítají se **hodiny, které si každý zapsal sám** (ne motohodiny). Pracovník vidí jen své, vedoucí všechny a může filtrovat podle pracovníka.

Pracovník tu má navíc dvě tabulky za **všechny stroje a všechny lidi**, ne jen za sebe:

- **Stroje**: kolik každý stroj v období odběhl (**Hodin za období**) a kolik **tun** zpracoval. Stejná čísla jako u vedoucího ve [Strojích](#stroje), jen bez cen.
- **Tankování**: kolikrát a kolik litrů se do každého stroje natankovalo, dole celkem.

Obě tabulky řídí jen datum ve filtru. Vedoucí je tu nemá, stejná čísla vidí ve Strojích.

### Moje poslední zápisy

Nahoře je pět zakázek, které jste naposledy **zapsali vy**, seřazené podle času zápisu. Ukazují stav: žluté *Čeká na schválení*, nebo *Schváleno*. Tady zjistíte, proč vám hodiny zatím chybí. Smazaná zakázka zmizí. Zakázky, kde jste jen spolupracovník, tu nejsou, objeví se vám po schválení v Odpracovaných hodinách. Filtry tuto tabulku neovlivňují. Vedoucí ji nemá.

> **Zobrazit vše:** tabulky v Souhrnu, Strojích a Materiálu ukazují nejdřív **pět řádků**. Je-li jich víc, je pod tabulkou tlačítko **Zobrazit vše** s jejich počtem. Filtry zůstanou. Export do Excelu obsahuje vždy všechny řádky.

> **Rychlé období:** nad každým filtrem jsou tlačítka **Vše**, **Posledních 7 dní**, **Posledních 30 dní** a **Minulý měsíc**. Ostatní filtry zůstanou. **Vše** filtr data zruší.

## Stažení do Excelu

Pod tabulkami je tlačítko **Stáhnout do CSV / Excelu**. Stáhne se tabulka **s nastaveným filtrem**: nejdřív nafiltrujte, zkontrolujte, pak stáhněte.

| Stránka | Tabulka |
|---|---|
| Souhrn | Odpracované hodiny |
| Stroje | Stav strojů, Tankování, Detail tankování, Detail používání strojů |
| Materiál | Souhrn materiálů, Detail položek |

- Stáhne se **celá tabulka**, i když jich je na stránce vidět jen pět. **Detailní tabulky** (Detail položek, Detail tankování, Detail používání strojů) se stáhnou celé, ne jen otevřená stránka. Hodí se k porovnání s dodacími listy a doklady za naftu.
- Soubor otevřete poklepáním. Excel ho rozdělí do sloupců a čísla umí sečíst.
- **Prázdná buňka znamená „nevíme"** (na stránce pomlčka). Nula se tam schválně nepíše, aby nepokazila součet.
- Jsou v něm jen schválené zakázky a pracovník stáhne jen své hodiny. Při chybném filtru je soubor bez řádků.

## Časté potíže

**Nahoře červeně „Vyplňte prosím všechna povinná pole."** nebo **„Formulář obsahuje chyby…"**
Zakázka se **neuložila**. Sjeďte níž, u pole je napsáno, co chybí nebo co je špatně. Uloženou zakázku poznáte podle zelené zprávy.

**„Zadejte číslo."**
Použili jste čárku. Pište s tečkou: `12.5`.

**„Zajistěte, aby tato hodnota byla 0.5 násobkem…"**
Hodiny jen po půlhodinách. Zaokrouhlete.

**„Vyplňte materiál i množství, nebo řádek nechte prázdný."**
Rozepsaný řádek. Doplňte ho, nebo vymažte. Totéž u spolupracovníků.

**„Přidejte alespoň jednu položku spotřeby a jednu položku výroby."** / **„Celkové množství spotřeby a výroby se musí rovnat."**
Viz [Spotřeba a výroba](#spotřeba-a-výroba). V závorce hlášky jsou oba součty.

**„Vyplňte stroj i litry, nebo řádek nechte prázdný."**
Rozepsaný řádek tankování. Doplňte ho, nebo vymažte.

**Zakázku nevidím v Souhrnu** / **tankování nevidím ve Strojích.**
Čeká na schválení.

**Nevidím Přehled, Stroje ani Materiál** / **nevidím hodiny ani zakázky kolegů.**
Jsou jen pro vedoucí. Požádejte správce o změnu role.

**V nabídce chybí materiál, stroj, lokace nebo zdroj.**
Byl vyřazen. Správce ho může znovu aktivovat.

**Odeslané fotky tam nejsou.**
Formulář něco vytkl a fotky se ztratily. Vyberte je znovu.

**„Přípona souboru … není povolena."** / **„… Soubor se nepodařilo otevřít jako fotku."**
Soubor není fotka, nebo je poškozený. Použijte `jpg`, `png`, `webp` nebo `heic`.

**„… Fotka je příliš velká"** / **„Fotky jsou dohromady příliš velké"** / **„Zakázka může mít nejvýše 10 fotek"**
Pošlete menší nebo méně fotek. Při úpravě případně nejdřív odeberte uloženou.

**„Vybrat z galerie" nenabízí fotoaparát.**
Zvolte **Vyfotit**.

**Špatně zadaná zakázka.**
Řekněte vedoucímu. Opraví ji v Přehledu, nebo ji smaže.
