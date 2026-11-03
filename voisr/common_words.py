"""Common English, for two jobs: leaving ordinary words out of the context
terms, and refusing to auto-add them to the dictionary."""
import re
from pathlib import Path

SYSTEM_WORDS = Path("/usr/share/dict/words")

_COMMON = frozenset("""
a able about above accept access account across act action active actually
add address after afternoon again against age ago agree ahead air all allow
almost alone along already also although always am among amount an and angry
animal another answer any anyone anything anyway app apple apply appointment
april area arm around arrive art article as ask at attach attention august
aunt author available away baby back bad bag ball bank bar base be beach
bear beautiful became because become bed been before began begin behind
being believe below best better between big bill bird bit black blue board
boat body book born both bottom box boy break bring brother brown budget
build building business busy but button buy by call came camera can cancel
cannot car card care carry case cat catch cause center certain chair chance
change channel chapter charge chat check child children choose church city
class clean clear click client clock close cloud club code coffee cold
college color come comment common company complete computer condition confirm
consider contact contain continue control cook cool copy corner correct cost
could count country couple course cover create cross current customer cut
daily dark data date daughter day dead deal dear december decide deep default
delete deliver design desk detail develop did die different difficult dinner
direct discuss do doctor document does dog doing done door double down
download draft draw dream drink drive drop during each ear early earth east
easy eat edit edge education effect eight either else email employee end
enough enter error even evening event ever every everyone everything example
except expect experience explain eye face fact fall family far farm fast
father favorite february feature feel feet few field figure file fill final
find fine finish fire first fish five fix floor flow fly folder follow food
foot for force forget form forward found four free friday friend from front
full fun future game garden gave general get girl give glad glass go god
goes going gone good got government great green group grow guess guy had hair
half hall hand happen happy hard has hat have he head hear heard heart heavy
held hello help her here herself hey hi high him himself his history hit hold
home hope hospital hot hour house how however huge human hundred husband i
idea if image important in include inside instead interest internet into
invoice is issue it item its itself january job join july jump june just keep
key kid kill kind kitchen knew know lady land language large last late later
law lay lead learn least leave left leg less let letter level life light like
line link list listen little live local long look lose lost lot love low
lunch machine made mail main make man manager many march mark market may maybe
me mean meet meeting member message met method middle might mile million mind
minute miss mobile moment monday money month more morning most mother mouse
mouth move movie much music must my myself name near need never new news next
nice night nine no none nor north not note nothing notice november now number
of off offer office often oh ok okay old on once one only open option or order
other our out outside over own page paid pain paper parent park part party pass
password past pay payment people per perhaps person phone photo pick picture
piece place plan play please point police poor popular possible post pound
power present press pretty price print private probably problem process
product program project provide public pull purpose push put quality question
quick quite radio rain raise ran rate rather reach read ready real really
reason receive recent record red remember remove reply report request result
return review rich right ring river road room round rule run sad said sale same
saturday save saw say school science screen search second see seem seen select
sell send sent september serious service set settings seven several shall she
ship shop short should show side sign simple since sing sister sit site six
size sleep slow small smile so some someone something sometimes son song soon
sorry sound south space speak special spend sport spring stand star start state
stay step still stop store story street strong student study stuff such summer
sun sunday support sure system table take talk tax teacher team tell ten term
test text than thank thanks that the their them themselves then there these
they thing think third this those though thought thousand three through
thursday time to today together told tomorrow tonight too took top total touch
toward town track trade travel tree trip true try tuesday turn two type uncle
under understand unit until up update upon us use used user usual very video
view visit voice wait walk wall want war warm was wash watch water way we
wear website wednesday week weekend welcome well went were west what when where
whether which while white who whole whose why wife will win window winter wish
with within without woman wonder word work world would write written wrong yeah
year yellow yes yesterday yet you young your yourself zero
australia america american australian britain british canada china england
english europe european france french germany german india italian japan
japanese mr mrs ms dr sir madam inc ltd llc pty the
""".split())

_SUFFIXES = ("'s", "’s", "s", "es", "ed", "d", "ing", "ly", "er", "est")


def _forms(word: str):
    yield word
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 2:
            stem = word[:-len(suffix)]
            yield stem
            yield stem + "e"  # liking -> like
            if len(stem) > 2 and stem[-1] == stem[-2]:
                yield stem[:-1]  # stopped -> stop
            if stem.endswith("i"):
                yield stem[:-1] + "y"  # replies -> reply


def is_common_word(word: str) -> bool:
    lowered = word.lower()
    return any(form in _COMMON for form in _forms(lowered))


def is_everyday_lowercase_word(word: str, words_path: Path = SYSTEM_WORDS) -> bool:
    """Is this spelled in the system dictionary as an ordinary lowercase word?
    Names are capitalised there, so "claude" is not and "clod" is. Read on
    demand with nothing kept: this runs a few times a day at most, and 2.4 MB
    of words shouldn't sit in memory on an 8 GB Mac. A dictionary that can't
    be read answers True, which refuses the auto-add."""
    try:
        words = words_path.read_bytes()
    except OSError:
        return True
    return any(
        re.search(rb"^" + re.escape(form.encode("utf-8")) + rb"$", words, re.MULTILINE)
        for form in _forms(word.lower()))
