from mai.cppindex import file_symbols, find_function, function_covering, functions

# Source (Three-like): multilocale layout — AutoProduceStrings takes `loc`.
SOURCE = b"""
#define MAX_LOCALE 16

char* DBCFileLoader::AutoProduceStringsArrayHolders(const char* format, char* dataTable)
{
    return dataTable;
}

char* DBCFileLoader::AutoProduceStrings(const char* format, char* dataTable, LocaleConstant loc)
{
    for (int y = 0; y < recordCount; ++y)
    {
        int offset = recordOffset(y);
        char const** holder = *(char const***)(&dataTable[offset]);
        char const* st = getRecord(y).getString(x);
        if (st && *st)
            holder[loc] = stringPool + (st - (const char*)stringTable);
    }
    return stringPool;
}
"""

# Target (Zero-like): single-pointer layout — no `loc`, no holder function.
TARGET = b"""
char* DBCFileLoader::AutoProduceStrings(const char* format, char* dataTable)
{
    for (int y = 0; y < recordCount; ++y)
    {
        int offset = recordOffset(y);
        char* slot = stringPool + (getRecord(y).getString(x) - (const char*)stringTable);
        *((char**)(&dataTable[offset])) = slot;
    }
    return stringPool;
}
"""


def test_functions_finds_member_with_pointer_return():
    names = {f.name for f in functions(SOURCE)}
    assert "AutoProduceStrings" in names
    assert "AutoProduceStringsArrayHolders" in names


def test_qualified_name_preserved():
    fn = find_function(SOURCE, "AutoProduceStrings")
    assert fn is not None
    assert fn.qualified_name == "DBCFileLoader::AutoProduceStrings"


def test_param_extraction_source_has_loc():
    fn = find_function(SOURCE, "AutoProduceStrings")
    assert fn.params == ["format", "dataTable", "loc"]


def test_param_extraction_target_lacks_loc():
    fn = find_function(TARGET, "AutoProduceStrings")
    assert "loc" not in fn.params
    assert fn.params == ["format", "dataTable"]


def test_locals_capture_patch_introduced_names():
    fn = find_function(SOURCE, "AutoProduceStrings")
    assert {"holder", "st", "offset", "y"} <= fn.scope_names


def test_loc_resolves_in_source_scope_only():
    src = find_function(SOURCE, "AutoProduceStrings")
    tgt = find_function(TARGET, "AutoProduceStrings")
    assert "loc" in src.scope_names
    assert "loc" not in tgt.scope_names
    assert "loc" not in file_symbols(TARGET)


def test_file_symbols_include_macro_and_functions():
    syms = file_symbols(SOURCE)
    assert "MAX_LOCALE" in syms
    assert "AutoProduceStringsArrayHolders" in syms
    assert "AutoProduceStrings" in syms


def test_function_covering_picks_enclosing():
    # The line of `holder[loc] = ...` sits inside AutoProduceStrings.
    target_line = next(
        i + 1 for i, ln in enumerate(SOURCE.splitlines())
        if b"holder[loc]" in ln)
    fn = function_covering(SOURCE, {target_line})
    assert fn is not None and fn.name == "AutoProduceStrings"
