# SU answer-key delta — the cells that came back as URLs

Only the tuition and admission cells. To save you opening two PDFs, the
**verbatim source rows** are quoted below, straight from the documents your
first pass linked to. These are the raw document rows, **not** anything the
pipeline produced — you are still judging the source, same as before.

Write the value that applies to each program. `NOT STATED` is a valid answer.

---

## Tuition — from the fee order (ТАКСИ 2026-2027, държавна поръчка)

Each row below is one verbatim table row: `направление | факултет | специалност |` then the fee columns as printed.

- **su-kn · Компютърни науки**
  source row: `Информатика и компютърни науки | ФМИ | Компютърни науки | 460 EUR | няма | 460 EUR | няма | 920 EUR | 460 EUR`
  value: ok

- **su-inf · Информатика**
  source row: `Информатика и компютърни науки | ФМИ | Информатика | 460 EUR | няма | 460 EUR | няма | 920 EUR | 460 EUR`
  value: ok

- **su-psy · Психология**
  source row: `Психология | ФФ | Психология | 310 EUR | няма | 310 EUR | 200 EUR | 770 EUR | 770 EUR`
  value: ok

- **su-ch · Компютърна химия**
  source row: `Химически науки | ФХФ | Компютърна химия | освободени | освободени | освободени | освободени | 460 EUR | 260 EUR`
  value: ok

- **su-cult · Културология**
  source row: `Социология, антропология и науки за културата | ФФ | Културология | освободени 310 EUR | няма | 770 EUR | 770 EUR`
  ⚠️ This row's third fee cell came out of the PDF **merged** — it reads
  `освободени 310 EUR` as one cell, and the row has fewer cells than its
  neighbours, so the columns don't line up with the Психология row above.
  Please say which applies (освободени, or 310 EUR), or `AMBIGUOUS` if the
  document itself doesn't make it decidable.
  value: 310 EUR 

## Admission — from Приложение 2 (балообразуване)

Verbatim lines, as printed in the ordinance:

- **su-kn · Компютърни науки**
  source line: `Компютърни науки Редовна 1. изпит по математика Математика`
  value: ok

- **su-inf · Информатика**
  source line: `Информатика Редовна С коефициент 3,25:`
  value: ok

- **su-psy · Психология**
  source line: `Психология Редовна С коефициент 3: Английски език`
  value: ok

- **su-cult · Културология**
  source line: `Културология Редовна С коефициент 3: История и цивилизации`
  value: ok

*(su-ch · Компютърна химия already has a full admission quote from your first
pass — nothing needed here.)*
