# Your PUB Converter Said "Success." Two Files Were Empty.

This week, a competitor published something genuinely useful: the team at [devaland](https://dev.to/devaland/microsoft-publisher-is-retiring-we-converted-25-real-pub-files-to-see-what-survives-3897) converted 25 real `.pub` files through a free converter and reported what survived — updating the piece today, October 13, 2026, as Publisher 2021 support ends. Buried in their results is the single most important sentence in the whole Publisher migration conversation: **"Two files reported success and produced no PDF at all."**

Read that again. The tool said success. The output didn't exist.

If you're batch-converting a Publisher archive this week — and half the internet is, with Publisher 2021 support ending October 13, 2026 — this is the failure mode that should keep you up at night. Not the files that error out loudly. The ones that lie to you.

## Why "Success" Lies

Most converters report on the *process*, not the *product*. Did the conversion routine finish without crashing? Then it's a success. Nobody checks whether the PDF actually contains your document.

In a batch of 200 files, this is devastating. Five broken files look exactly like 200 good ones in a folder listing. You delete the originals, archive the PDFs, and discover the damage months later when you need the file — when the original is gone and your memory of what's in it has faded.

Silent failures come in three flavors, from worst to sneakiest:

1. **Empty output.** The converter reports success; the PDF is 0 bytes or was never written. This is what the 25-file test caught — twice.
2. **Dropped content.** The PDF exists and opens fine, but images, tables, or whole pages vanished. When we built our own converter, our test newsletter came through with 9 images reduced to 1 — and the first version of our tool stamped it "ok." That embarrassment is why we built verification in the first place.
3. **Scrambled text.** Characters extract in the wrong order or as mojibake. The PDF looks full, but search finds nothing and copy-paste produces garbage. You won't notice until you need that text.

## The Test That Proved the Point

Credit where it's due: the devaland team did the migration community a favor. They used real documents — decades-old Publisher files from open-source test suites, the kind of gnarly legacy files real archives are full of — and published honest results, including the two phantom successes. Their own words: *"The converter finished, reported success with exit code 0, and wrote nothing."*

But notice what their test *didn't* do: it didn't verify outputs as part of the conversion. The silent failures were discovered by a human looking at results afterward. That's fine for a 25-file experiment. It doesn't scale to your 400-file church newsletter archive.

And notice how far their own fix goes: they now count a job as done *"when a real PDF with at least one page is on disk."* That's an existence check — it catches the empty-output failures, and honestly, that's the most important one. But it wouldn't catch our 9→1 newsletter: the PDF existed, it had pages, it opened fine. It had just silently dropped eight images. Existence checks catch the files that lie about succeeding; they don't catch the files that succeed at producing the wrong document.

The missing step isn't better conversion. It's **verification as a separate pass**: after every file converts, check the output independently — not just "does a PDF exist," but "did the content survive" — and flag anything suspicious before you trust the batch.

## What Verification Actually Checks

"Verify" sounds vague. Here's what it concretely means, per file:

- **The output exists and isn't empty.** Sounds obvious. Catches failure mode #1 outright.
- **Page count is sane.** A 4-page newsletter that produced a 1-page PDF lost something. A 1-page flyer that produced 40 pages gained something weird.
- **Text survived.** Extract the text from the PDF and check it's non-trivial — not zero words, not a tenth of what the source had.
- **Images survived.** Count embedded images in the output and compare against the source. Our 9→1 newsletter disaster is exactly what this check exists to catch.
- **The file is a valid PDF.** Corrupt headers, truncated streams — the boring stuff that makes a file unopenable six months from now.

Each file gets a verdict: **passed**, **suspicious** (converted, but something looks off — review me), or **failed** (didn't convert). The report lists every suspicious and failed file by name. That's the whole product insight: *a converter that tells you which files it broke is more trustworthy than one that claims it broke none.*

## The Migration Playbook, Fixed

The standard advice — gather, batch-convert, spot-check a few — is how silent failures survive. Here's the corrected version:

1. **Gather everything.** Search all drives for `.pub`. You'll find twice as many as you expect.
2. **Batch-convert to PDF.** This is the right first move; doing 300 files one at a time is how migrations die halfway.
3. **Read the verification report, not the success count.** Ignore "198 of 200 succeeded." Open the list of suspicious and failed files — that's your actual todo list.
4. **Re-run the flagged files individually.** A file that failed in batch often converts fine alone, with different settings, or via LibreOffice Draw opened by hand. The report tells you exactly which files deserve the effort.
5. **Spot-check the "passed" files that matter most.** Verification catches the systematic failures; for your ten most important documents, still open the PDF and glance at it. Trust, but verify the crown jewels.

## Why This Week Especially

October 13, 2026 is the end of support for Publisher 2021. Everyone with a Publisher archive is converting right now, which means everyone is exposed to silent failures right now — at the exact moment they're least likely to double-check, because the deadline creates urgency and urgency kills diligence.

Don't let the news cycle rush you into a bad archive. Batch-convert this week, absolutely — but convert with verification, keep the originals until the report is clean, and treat any tool that reports 100% success on a large batch with suspicion. In our experience, 100% success on legacy files usually means 100% unchecked.

Try the free converter to get started — single-file conversion is free, and every file gets the same verification pass, free or paid. If a file looks wrong, we'd rather tell you than take your money.

## FAQ

**What is a silent conversion failure?**
When a file converter reports success but the output is empty, incomplete, or corrupted — no error, no warning. A public 25-file test this month found 2 of 25 files "succeeded" with no PDF produced at all.

**Can't I just open each PDF and check?**
For 10 files, yes. For 200, no — that's the entire problem batch conversion exists to solve. Automated per-file verification (output exists, page count sane, text and images survived) scales where eyeballing doesn't.

**Why would images disappear in conversion?**
Legacy `.pub` files embed images in formats modern libraries parse imperfectly. The converter extracts what it can and silently drops the rest. Without an image-count check, the loss is invisible until someone opens the PDF.

**Should I keep my original .pub files after converting?**
Yes — until the verification report is clean. Disk space is cheap; the only copy of a 2008 fundraiser program is not. Archive the verified PDFs as your working copies and keep the originals as the disaster-recovery backstop.

**Does verification slow down batch conversion?**
Marginally — seconds per file for the checks. Compared to the cost of discovering a broken archive months later, it's the cheapest insurance in the migration.
