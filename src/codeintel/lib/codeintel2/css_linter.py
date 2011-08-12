# Copyright (c) 2000-2011 ActiveState Software Inc.
#
# Contributors:
#   Eric Promislow (EricP@ActiveState.com)

"""Parse CSS/Less/SCSS for linting purposes only"""

import copy, os, sys, traceback, re, time
import logging
import SilverCity
from SilverCity import CSS, ScintillaConstants
from codeintel2.shared_lexer import EOF_STYLE, Lexer
from codeintel2.lang_css import CSSLangIntel

log = logging.getLogger("css_linter")
#log.setLevel(logging.DEBUG)

# This class is by both the parser and lexer
class _CSSLexerClassifier(object):

    def is_attribute(self, tok):
        return tok.style == ScintillaConstants.SCE_CSS_ATTRIBUTE

    def is_directive(self, tok):
        return tok.style == ScintillaConstants.SCE_CSS_DIRECTIVE

    def is_identifier(self, tok):
        return tok.style in (ScintillaConstants.SCE_CSS_IDENTIFIER,
                             ScintillaConstants.SCE_CSS_IDENTIFIER2,
                             ScintillaConstants.SCE_CSS_IDENTIFIER3,
                             ScintillaConstants.SCE_CSS_EXTENDED_IDENTIFIER,
                             ScintillaConstants.SCE_CSS_EXTENDED_PSEUDOELEMENT,
                             ScintillaConstants.SCE_CSS_UNKNOWN_IDENTIFIER)

    def is_unknown_identifier(self, tok):
        return tok.style == ScintillaConstants.SCE_CSS_UNKNOWN_IDENTIFIER

    def is_special_identifier(self, tok):
        return tok.style in (ScintillaConstants.SCE_CSS_ID,
                             ScintillaConstants.SCE_CSS_CLASS,
                             ScintillaConstants.SCE_CSS_PSEUDOCLASS,
                             ScintillaConstants.SCE_CSS_PSEUDOELEMENT,
                             ScintillaConstants.SCE_CSS_UNKNOWN_PSEUDOCLASS,
                             ScintillaConstants.SCE_CSS_EXTENDED_PSEUDOCLASS,
                             ScintillaConstants.SCE_CSS_EXTENDED_PSEUDOELEMENT,)

    def is_important(self, tok, text):
        return (tok.style == ScintillaConstants.SCE_CSS_IMPORTANT
                and tok.text == text) 

    _number_re = re.compile(r'-?(?:\d+(?:\.\d*)?|\.\d+)')
    def is_number(self, tok):
        return (tok.style == ScintillaConstants.SCE_CSS_NUMBER
                or (tok.style == ScintillaConstants.SCE_CSS_VALUE
                    and self._number_re.match(tok.text)))

    def is_operator(self, tok, target=None):
        if tok.style != ScintillaConstants.SCE_CSS_OPERATOR:
            return False
        elif target is None:
            return True
        else:
            return target == tok.text
        
    def is_operator_choose(self, tok, targets):
        if tok.style != ScintillaConstants.SCE_CSS_OPERATOR:
            return False
        else:
            return tok.text in targets

    def is_string(self, tok):
        return tok.style in (ScintillaConstants.SCE_CSS_DOUBLESTRING,
                             ScintillaConstants.SCE_CSS_SINGLESTRING,)

    def is_stringeol(self, tok):
        return tok.style == ScintillaConstants.SCE_CSS_STRINGEOL

    def is_tag(self, tok):
        return (tok.style == ScintillaConstants.SCE_CSS_TAG
                or self.is_operator(tok, "*"))

    def is_value(self, tok, target=None):
        if not (tok.style in (ScintillaConstants.SCE_CSS_VALUE,
                              ScintillaConstants.SCE_CSS_NUMBER)):
            return False
        elif target is None:
            return True
        else:
            return tok.text == target

    def is_comment(self, ttype):
        return ttype in (ScintillaConstants.SCE_CSS_COMMENT,)
        
    @property
    def style_comment(self):
        return ScintillaConstants.SCE_CSS_COMMENT
        
    @property
    def style_default(self):
        return ScintillaConstants.SCE_CSS_DEFAULT

    @property
    def style_operator(self):
        return ScintillaConstants.SCE_CSS_OPERATOR

_classifier = None

    # Routines that shared_lexer require:

# No need for a UDL class -- since everything here goes through
# SilverCity, it always uses pure styles.

class SyntaxErrorEOF(SyntaxError):
    pass

def get_silvercity_lexer(language):
    return {"CSS": CSS.CSSLexer,
            "SCSS": CSS.SCSSLexer,
            "Less": CSS.LessLexer}.get(language, CSS.CSSLexer)

class _CSSLexer(Lexer):
    def __init__(self, code, language):
        Lexer.__init__(self)
        # We don't care about any JS operators in `...` escapes
        terms = '@{ ${ ~= |= ::'
        if language == "Less":
            terms += ' || &&'
        self.multi_char_ops = self.build_dict(terms)
            
        self.classifier = _classifier
        get_silvercity_lexer(language)().tokenize_by_style(code, self._fix_token_list)
        self.string_types = [ScintillaConstants.SCE_CSS_DOUBLESTRING,
                         ScintillaConstants.SCE_CSS_SINGLESTRING,
                         ]
        
    def _fix_token_list(self, **tok):
        
        ttype = tok['style']
        tval = tok['text']
        if ttype == ScintillaConstants.SCE_CSS_OPERATOR and len(tval) > 1:
            self.append_split_tokens(tok, self.multi_char_ops, self.q)
        else:
            self.q.append(tok)

    def append_split_tokens(self, tok, multi_char_ops_dict, dest_q):
        # This function doesn't subtract 1 from `col + len(stxt)`
        tval = tok['text']
        split_tokens = []
        while len(tval) > 0:
            if multi_char_ops_dict.has_key(tval):
                split_tokens.append(tval)
                break
            else:
                #XXX Handle allowed prefixes, as in "<<" and "<<="
                found_something = False
                for possible_op in multi_char_ops_dict.keys():
                    if tval.startswith(possible_op):
                        split_tokens.append(possible_op)
                        tval = tval[len(possible_op):]
                        found_something = True
                        break
                if not found_something:
                    split_tokens.append(tval[0])
                    tval = tval[1:]
        if len(split_tokens) > 1:
            col = tok['start_column']
            for stxt in split_tokens:
                new_tok = copy.copy(tok)
                new_tok['text'] = stxt
                new_tok['start_column'] = col
                new_tok['end_column'] = col + len(stxt)
                col = new_tok['end_column']
                dest_q.append(new_tok)
        else:
            dest_q.append(tok)
            
    def next_token_is_whitespace(self, tok):
        if not self.q:
            return False
        return self.q[0]['style'] in (EOF_STYLE, self.classifier.style_comment,
                                   self.classifier.style_default)

    def get_next_token(self):
        res = Lexer.get_next_token(self)
        # Column adjustment
        #print "get_next_token: " + res.dump_ret()
        if res.start_column is not None:
            res.end_column = res.start_column + len(res.text)
        return res            
        
class Result(object):
    """
    Status: 1 for errors, 0 for warnings.  Default is warning
    """
    def __init__(self, message, line_start, col_start, line_end, col_end, status=1):
        self.message = message
        if line_start is not None:
            if line_end < line_start:
                line_end = line_start
            if line_start == line_end and col_end <= col_start:
                col_end = col_start + 1
        self.line_start = line_start
        self.col_start = col_start
        self.line_end = line_end
        self.col_end = col_end
        self.status = status

    def __str__(self):
        if self.line_start is None:
            return "%s: %s: EOF" % ((self.status == 1 and "Error" or "Warning"),
                                    self.message)
        else:
            return "%s: %s: <%d:%d> = <%d:%d>" % ((self.status == 1 and "Error" or "Warning"),
                                              self.message,
                                          self.line_start, self.col_start,
                                          self.line_end, self.col_end)

class _CSSParser(object):

    _PARSE_REGION_AT_START = 0
    _PARSE_REGION_SAW_CHARSET = 1
    _PARSE_REGION_SAW_IMPORT = 2
    _PARSE_REGION_SAW_OTHER = 3
    
    def __init__(self, language):
        self._supportsNestedDeclaration = language in ("Less", "SCSS")
        self.language = language

    def _add_result(self, message, tok, status=1):
        self._add_result_tok_parts(message,
                                   tok.start_line, tok.start_column,
                                   tok.end_line, tok.end_column, tok.text,
                                   status)

    def _add_result_tok_parts(self, message, line_start, col_start, line_end, col_end, text, status=1):
        if not self._results or self._results[-1].line_end < line_start:
            if not "got" in message:
                if line_start is None:
                    message += ", reached end of file"
                elif text:
                    message += ", got '%s'" % (text)
            self._results.append(Result(message, line_start, col_start, line_end, col_end, status))
    
    def parse(self, text):
        self.token_q = []
        self._results = []
        global _classifier
        if _classifier is None:
            _classifier = _CSSLexerClassifier()
        self._classifier = _classifier
        self._tokenizer = _CSSLexer(text, self.language)
        if self.language == "Less":
            self._less_mixins = {} # => name => parameter list
        self._parse()
        return self._results

    def _parser_putback_recover(self, tok):
        self._tokenizer.put_back(tok)
        raise SyntaxError()
        
    def _parse(self):
        self._at_start = True
        self._charset = "UTF-8"
        self._parse_top_level()            

    def _parse_ruleset(self):
        self._parse_selector()

        while True:
            tok = self._tokenizer.get_next_token()
            if tok.style == EOF_STYLE:
                self._add_result("expecting a block of declarations", tok)
                return
            self._check_tag_tok(tok, 1)
            if not self._classifier.is_operator(tok, ","):
                break
            self._parse_selector()
        self._parse_declarations(tok) # tok is the non-comma, should be "{"
        
    def _parse_selector(self, resolve_selector_property_ambiguity=False):
        """
        selector : simple_selector [ combinator selector
                                     | S [ combinator? selector ]?
                                   ]? ;
        simple_selector : element_name [HASH | class | attrib | pseudo ]*
                          | [HASH | class | attrib | pseudo ]+;

        Instead, here we'll loop through selectors, allowing
        combinators if we find them.

        Note that error-recovery takes place here, not at the top-level.
        """
        require_simple_selector = True
        while True:
            res = self._parse_simple_selector(require_simple_selector,
                                              resolve_selector_property_ambiguity)
            if not res:
                break
            if resolve_selector_property_ambiguity and not require_simple_selector:
                # More than one simple selector in a row means it's not a property
                self._saw_selector = True
            require_simple_selector = False
            tok = self._tokenizer.get_next_token()
            self._check_tag_tok(tok, 2)
            if not self._classifier.is_operator_choose(tok, ("+",">")):
                self._tokenizer.put_back(tok)
                
    def _pseudo_element_check(self, tok, saw_pseudo_element):
        if saw_pseudo_element:
            self._add_result_tok_parts("Pseudo elements must appear at the end of a selector chain",
                                       tok.start_line, tok.start_column,
                                       tok.end_line, tok.end_column,
                                       "", 1)
    
    def _parse_simple_selector(self, match_required, resolve_selector_property_ambiguity):
        saw_pseudo_element = False
        current_name = None
        num_selected_names = 0
        could_have_mixin = False
        while True:
            tok = self._tokenizer.get_next_token()
            if tok.style == EOF_STYLE:
                break
            self._check_tag_tok(tok, 3)
            log.debug("_parse_simple_selector: got tok %s", tok.dump_ret())
            if self._classifier.is_tag(tok):
                num_selected_names += 1
                self._pseudo_element_check(tok, saw_pseudo_element)
                current_name = tok.text
                could_have_mixin = False
            elif self._classifier.is_identifier(tok):
                num_selected_names += 1
                self._pseudo_element_check(tok, saw_pseudo_element)
                if not self._supportsNestedDeclaration:
                    self._add_result("treating unrecognized name %s (%d) as a tag name" % (tok.text, tok.style),
                                     tok, status=0)
                current_name = tok.text
                could_have_mixin = False
            elif self._classifier.is_operator(tok):
                if tok.text == ":":
                    if resolve_selector_property_ambiguity and not self._saw_selector:
                        # This is the crucial point
                        # Are we looking at
                        # font: ....
                        #    or
                        # a:hover ...
                        # We take the easy way out and resolve this by looking at coordinates
                        #
                        # We also assume that anyone using Less or SCSS is more interested in
                        # readability than conciseness, so we aren't dealing with minified CSS.
                        if self._tokenizer.next_token_is_whitespace(tok):
                            self._tokenizer.put_back(tok)
                            return False
                    prev_tok = tok
                    tok = self._tokenizer.get_next_token()
                    if not self._check_special_identifier(prev_tok, tok):
                        return False
                    num_selected_names += 1
                    current_name = tok.text
                    if tok.text == "not":
                        tok = self._tokenizer.get_next_token()
                        if self._classifier.is_operator(tok, "("):
                            # It's the CSS3 "not" selector
                            self._parse_simple_selector(True, resolve_selector_property_ambiguity=False)
                            self._parse_required_operator(")")
                        else:
                            self._tokenizer.put_back(tok)
                    could_have_mixin = False
                elif tok.text in ("#", ".", "::",):
                    prev_tok = tok
                    tok = self._tokenizer.get_next_token()
                    if not self._check_special_identifier(prev_tok, tok):
                        return False
                    num_selected_names += 1
                    self._pseudo_element_check(tok, saw_pseudo_element)
                    current_name = tok.text
                    could_have_mixin = (self.language == "Less"
                                        and prev_tok.text == '.'
                                        and num_selected_names == 1)
                    if prev_tok.text == "::":
                        saw_pseudo_element = True
                elif tok.text == '[':
                    if resolve_selector_property_ambiguity:
                        # More than one simple selector in a row means it's not a property
                        self._saw_selector = True
                    self._parse_attribute()
                    num_selected_names += 1
                    could_have_mixin = False
                elif tok.text == '{':
                    if num_selected_names == 0 and match_required:
                        self._add_result("expecting a selector, got '{'", tok)
                    # Short-circuit the calling loop.
                    self._tokenizer.put_back(tok)
                    return False
                elif tok.text == '}':
                    if could_have_mixin and self._less_mixins.has_key(current_name):
                        self._inserted_mixin = True
                        self._tokenizer.put_back(tok)
                        return False
                    # assume we recovered to the end of a "}"
                    could_have_mixin = False
                    num_selected_names = 0
                    continue
                elif tok.text == "&" and self.language == "Less":
                    tok = self._tokenizer.get_next_token()
                    if (self._classifier.is_operator_choose(tok, ("#", ".", ":", "::"))
                        or self._classifier.is_special_identifier(tok)):
                        # Parse the qualifier next time around
                        self._saw_selector = True
                        num_selected_names += 1
                    else:
                        self._add_result("expecting a class or psuedo-class selector after '&'", tok)
                    self._tokenizer.put_back(tok)
                else:
                    break
            else:
                break
        if num_selected_names == 0:
            if match_required:
                self._add_result("expecting a selector, got %s" % (tok.text,), tok)
                tok = self._recover(allowEOF=False, opTokens=("{", "}"))
            # We got a { or }, so short-circuit the calling loop and
            # go parse the declaration
            self._tokenizer.put_back(tok)
            return False
        # Allow either the Mozilla ( id [, id]* ) property syntax or a Less mixin declaration/insertion
        # Note that we have the token that caused us to leave the above loop
        if not self._classifier.is_operator(tok, "("):
            if (could_have_mixin
                and self._less_mixins.has_key(current_name)
                and self._classifier.is_operator(tok, ";")):
                self._inserted_mixin = True
            self._tokenizer.put_back(tok)
            return True
        do_recover = False
        if could_have_mixin:
            if self._less_mixins.has_key(current_name):
                self._parse_mixin_invocation()
                self._inserted_mixin = True
            else:
                self._parse_mixin_declaration(current_name)
            return
        tok = self._tokenizer.get_next_token()
        if not self._classifier.is_tag(tok):
            self._add_result("expecting a property name", tok)
            self._tokenizer.put_back(tok)
            do_recover = True
        else:
            self._parse_identifier_list(self._classifier.is_tag, ",")
            tok = self._tokenizer.get_next_token()
            if not self._classifier.is_operator(tok, ")"):
                self._add_result("expecting ')'", tok)
                do_recover = True
        if do_recover:
            tok = self._recover(allowEOF=False, opTokens=("{", "}"))
            self._tokenizer.put_back(tok)
            return False
        return True
    
    def _check_special_identifier(self, prev_tok, tok):
        if (self._classifier.is_special_identifier(tok)
            or (self._supportsNestedDeclaration
                and self._classifier.is_unknown_identifier(tok))):
            return True
        self._add_result("expecting an identifier after %s, got %s" % (prev_tok.text, tok.text), tok)
        # Give up looking at selectors
        self._tokenizer.put_back(tok)
        return False
                        
    def _parse_attribute(self):
        tok = self._tokenizer.get_next_token()
        if not (self._classifier.is_attribute(tok)
                or self._classifier.is_identifier(tok)):
            self._add_result("expecting an identifier", tok)
        else:
            tok = self._tokenizer.get_next_token()
        attr_toks = ("]", "=", "~=", "|=")
        if not self._classifier.is_operator_choose(tok, attr_toks):
            self._add_result("expecting one of %s" % (', '.join(attr_toks),), tok)
            self._parser_putback_recover(tok)
        if tok.text == ']':
            return
        tok = self._tokenizer.get_next_token()
        if self._classifier.is_stringeol(tok):
            self._add_result("missing string close-quote", tok)
        elif not (self._classifier.is_identifier(tok)
                or self._classifier.is_string(tok)):
            self._add_result("expecting an identifier or string", tok)
            self._tokenizer.put_back(tok)
            return
        tok = self._tokenizer.get_next_token()
        if not self._classifier.is_operator(tok, ']'):
            self._add_result("expecting an ']'", tok)        
            
        
    def _parse_assignment(self):
        """
        we saw $var or @var at top-level, expect : expression ;
        """
        self._parse_required_operator(":")
        self._parse_expression()
        self._parse_required_operator(";")

    def _parse_directive(self, prev_tok):
        tok = self._tokenizer.get_next_token()
        if not self._classifier.is_directive(tok):
            if (self._classifier.is_tag(tok)
                and (prev_tok.end_line != tok.start_line or
                     prev_tok.end_column != tok.start_column)):
                self._add_result_tok_parts("expecting a directive immediately after @",
                                 prev_tok.end_line,
                                 prev_tok.end_column,
                                 tok.start_line,
                                 tok.start_column, "")
            else:
                self._add_result("expecting an identifier after %s" % (prev_tok.text), tok)
                self._parser_putback_recover(tok)
            
        if tok.text == "charset":
            return self._parse_charset(tok)

        elif tok.text.lower() == "import":
            if self._region > self._PARSE_REGION_SAW_IMPORT:
                self._add_result("@import allowed only near start of file",
                                 tok)
            elif self._region < self._PARSE_REGION_SAW_IMPORT:
                self._region = self._PARSE_REGION_SAW_IMPORT
            return self._parse_import()

        self._region = self._PARSE_REGION_SAW_OTHER
        if tok.text.lower() == "media":
            self._parse_media()

        elif tok.text.lower() == "page":
            self._parse_page()
            
        elif self.language == "Less":
            self._parse_assignment()
        elif self.language == "SCSS":
            self._parse_scss_mixin_declaration(tok)
        else:
            self._add_result("expecting a directive after %s" % (prev_tok.text), tok)
            
    def _parse_scss_mixin_declaration(self, tok):
        if not (self._classifier.is_directive(tok) and tok.text == "mixin"):
            self._add_result("expecting a directive or 'mixin'", tok)
            self._parser_putback_recover(tok)
        tok = self._tokenizer.get_next_token()
        if not self._classifier.is_tag(tok):
            self._add_result("expecting a mixin name", tok)
            self._parser_putback_recover(tok) 
        tok = self._tokenizer.get_next_token()           
        if self._classifier.is_operator(tok, "("):
            self._parse_mixin_invocation()
        else:
            self._tokenizer.put_back(tok)
        self._parse_declarations()
    
    def _parse_required_operator(self, op, tok=None):
        if tok is None:
            tok = self._tokenizer.get_next_token()
        if not self._classifier.is_operator(tok, op):
            self._add_result("expecting '%s'" % op, tok)
            self._parser_putback_recover(tok)

    def _parse_charset(self, charset_tok):
        tok = self._tokenizer.get_next_token()
        if self._classifier.is_stringeol(tok):
            self._add_result("missing string close-quote", tok)
        elif not self._classifier.is_string(tok):
            self._add_result("expecting a string after @charset, got %s" % (tok.text),
                             tok)
            self._parser_putback_recover(tok)
        self._parse_required_operator(';')
        
        if self._region > self._PARSE_REGION_AT_START:
            self._add_result("@charset allowed only at start of file", charset_tok)
        else:
            self._region = self._PARSE_REGION_SAW_CHARSET


    def _parse_import(self):
        if (not self._parse_url()) and (not self._parse_string()):
            tok = self._tokenizer.get_next_token()
            self._add_result("expecting a string or url", tok)
            # Stay here, hope for the best.
        else:
            tok = self._tokenizer.get_next_token()
            
        if self._classifier.is_value(tok) and self._lex_identifier(tok):
            self._parse_identifier_list(self._classifier.is_value, ",")
            tok = self._tokenizer.get_next_token()
        self._parse_required_operator(";", tok)

    def _parse_media(self):
        tok = self._tokenizer.get_next_token()
        if not (self._classifier.is_value(tok) and self._lex_identifier(tok)):
            self._add_result("expecting an identifier for a media list", tok)
            self._parser_putback_recover(tok)
        self._parse_identifier_list(self._classifier.is_value, ",")
        self._parse_declarations()
        
    def _parse_page(self):
        tok = self._tokenizer.get_next_token()
        if self._classifier.is_operator(tok, ":"):
            tok = self._tokenizer.get_next_token()
            if not (self._classifier.is_special_identifier(tok)):
                self._add_result("expecting an identifier", tok)
                self._parser_putback_recover(tok)
            else:
                tok = None # refresh in _parse_declarations
        self._parse_declarations(tok)
        
    def _parse_mixin_declaration(self, current_name):
        """
        Allow ()
              (@foo[:value]) or
              (@foo1[:value1], @foo2[:value2], ... @fooN[:valueN])
        """
        
        mixin_vars = []
        self._less_mixins[current_name] = mixin_vars
        tok = self._tokenizer.get_next_token()
        if self._classifier.is_operator(tok, ")"):
            return
        while True:
            if not self._classifier.is_operator(tok, "@"):
                self._add_result("expecting ')' or a directive", tok)
                raise SyntaxError()
            tok = self._tokenizer.get_next_token()
            if not self._classifier.is_directive(tok):
                self._add_result("expecting a variable name", tok)
                raise SyntaxError()
            mixin_vars.append(tok.text)
            tok = self._tokenizer.get_next_token()
            if self._classifier.is_operator(tok, ":"):
                self._parse_expression()
                tok = self._tokenizer.get_next_token()
            if self._classifier.is_operator(tok, ","):
                tok = self._tokenizer.get_next_token()
                # Stay in loop
            elif self._classifier.is_operator(tok, ")"):
                return
            else:
                self._add_result("expecting ',' or ')'", tok)
                raise SyntaxError()
                
    def _parse_mixin_invocation(self):
        """
        comma-separated values, followed by a ")"
        """
        tok = self._tokenizer.get_next_token()
        if self._classifier.is_operator(tok, ")"):
            return
        self._tokenizer.put_back(tok)
        while True:
            self._parse_expression()
            tok = self._tokenizer.get_next_token()
            if self._classifier.is_operator(tok, ","):
                tok = self._tokenizer.get_next_token()
                # Stay in loop
            elif self._classifier.is_operator(tok, ")"):
                return
            else:
                self._add_result("expecting ',' or ')'", tok)
                raise SyntaxError()
        

    def _parse_declarations(self, tok=None):
        self._parse_required_operator("{", tok)
        while True:
            tok = self._tokenizer.get_next_token()
            if tok.style == EOF_STYLE:
                self._add_result("expecting '}', hit end of file", tok)
                raise SyntaxErrorEOF()
            if self._classifier.is_operator(tok, "}"):
                break
            self._tokenizer.put_back(tok)
            try:
                #TODO: Look ahead for either ';' or '{' to know
                # whether we're entering a nested block or a property
                # 
                # The problem with ':' is that they can appear in both selectors
                # as well as after property-names.
                if self._supportsNestedDeclaration:
                    self._parse_declaration_or_nested_block()
                else:
                    self._parse_declaration()
            except SyntaxError:
                tok = self._recover(allowEOF=False, opTokens=(';',"{","}"))
                t = tok.text
                if t == ";":
                    pass # This is good -- continue doing declarations.
                elif t == "}":
                    self._tokenizer.put_back(tok) # Use this back in loop
                elif t == "{":
                    # Either we're in less/scss, or we missed a selector, fake it
                    self._parse_declarations(tok);
                
    def _recover(self, allowEOF, opTokens):
        while True:
            tok = self._tokenizer.get_next_token()
            if tok.style == EOF_STYLE:
                if allowEOF:
                    return tok
                raise SyntaxErrorEOF()
            elif self._classifier.is_operator_choose(tok, opTokens):
                return tok

    def _parse_declaration(self):
        """
        Because this is called in a loop, have it return True only if it matches everything
        """
        self._parse_property()
        tok = self._tokenizer.get_next_token()
        if not self._classifier.is_operator(tok, ":"):
            self._add_result("expecting ':'", tok)
            # Swallow it
        self._parse_remaining_declaration()
        
    def _parse_remaining_declaration(self):
        """ SCSS allows nested declarations:
        li {
          font: {
            family: serif; // => font-family: serif; //etc.
            weight: bold;
            size: 1.2em;
          }
        }
        """
        if self.language == "SCSS":
            tok = self._tokenizer.get_next_token()
            have_brace = self._classifier.is_operator(tok, "{")
            self._tokenizer.put_back(tok)
            if have_brace:
                self._parse_declarations()
                return
                
        self._parse_expression()
        self._parse_priority()
        self._parse_required_operator(";")
    
    def _parse_scss_mixin_use(self):
        # Check for reading in a mixin
        tok = self._tokenizer.get_next_token()
        if not self._classifier.is_operator(tok, "@"):
            self._tokenizer.put_back(tok)
            return
        tok = self._tokenizer.get_next_token()
        if not (self._classifier.is_directive(tok) and tok.text == "include"):
            self._add_result("expecting 'include'", tok)
            self._tokenizer.put_back(tok)
            return
        tok = self._tokenizer.get_next_token()
        if not self._classifier.is_identifier(tok):
            self._add_result("expecting a mixin name", tok)
            self._parser_putback_recover(tok)
        tok = self._tokenizer.get_next_token()
        if self._classifier.is_operator(tok, "("):
            self._parse_mixin_invocation()
            tok = self._tokenizer.get_next_token()
        self._parse_required_operator(";", tok)
        return True
            
    def _parse_declaration_or_nested_block(self):
        """
        For Less and SCSS, blocks can nest.  So parse either a property-name
        or full-blown selector here.
        # Key method for Less/SCSS linting.  At this point we can have
        # either a declaration or a nested rule-set.
        """
        # selectors are supersets of property-names, so go with it
        self._saw_selector = False
        self._inserted_mixin = False
        if self.language == "SCSS":
            if self._parse_scss_mixin_use():
                return
        self._parse_selector(resolve_selector_property_ambiguity=True)
        tok = self._tokenizer.get_next_token()
        if self._classifier.is_operator(tok, ","):
            self._parse_ruleset()
            return
        if self._classifier.is_operator(tok, "{"):
            return self._parse_declarations(tok)
        if self._inserted_mixin:
            # Nothing left to do.
            # ; is optional before '}'
            if self._classifier.is_operator(tok, ";"):
                return
            elif self._classifier.is_operator(tok, "}"):
                self._tokenizer.put_back(tok)
                return
            
        if self._saw_selector:
            self._add_result("expecting '{'", tok)
        if self._classifier.is_operator(tok, ":"):
            self._parse_remaining_declaration()
        else:
            #@NO TEST YET
            self._add_result("expecting ':' or '{'", tok)
            self._parser_putback_recover(tok)

    def _parse_property(self):
        tok = self._tokenizer.get_next_token()
        if self._classifier.is_operator(tok, "*"):
            prev_tok = tok;
            tok = self._tokenizer.get_next_token()
        else:
            prev_tok = None
        if not (self._classifier.is_identifier(tok)
                or self._classifier.is_tag(tok)):
            #@NO TEST YET
            self._add_result("expecting a property name", tok)
            self._parser_putback_recover(tok)
        if prev_tok is not None:
            if prev_tok.end_column == tok.start_column:
                self._add_result_tok_parts("Use of non-standard property-name '%s%s'" %
                                           (prev_tok.text, tok.text),
                                           prev_tok.start_line, prev_tok.start_column,
                                           tok.end_line, tok.end_column, "",
                                           status=0)
            else:
                # Put the token back, trigger an error-message later
                self._tokenizer.put_back(tok)

    def _parse_expression(self):
        if self._parse_term(required=True):
            while True:
                self._parse_operator()
                if not self._parse_term(required=False):
                    break

    def _parse_operator(self):
        tok = self._tokenizer.get_next_token()
        self._check_tag_tok(tok, 7)
        if not self._classifier.is_operator(tok):
            self._tokenizer.put_back(tok)
        elif tok.text in (",", "/"):
            # use up
            pass
        elif self.language == "Less" and tok.text in ("~", "*", "^", "-", "+", "/", "|", "&", "||", "&&",):
            # use up
            pass
        else:
            self._tokenizer.put_back(tok)

    def _parse_unary_operator(self):
        tok = self._tokenizer.get_next_token()
        if not self._classifier.is_operator(tok):
            self._tokenizer.put_back(tok)
            return False
        elif not tok.text in ("+", "-"):
            self._tokenizer.put_back(tok)
            return False
        else:
            return True
    
    def _parse_parenthesized_expression(self):
        tok = self._tokenizer.get_next_token()
        if not self._classifier.is_operator(tok, "("):
            self._tokenizer.put_back(tok)
            return False
        self._parse_expression()
        self._parse_required_operator(")")
        return True
    
    def _parse_escaped_string(self):
        """
        Accept any of
        ~" ... "
        ~` ... `
        ` ... `
        """
        tok = self._tokenizer.get_next_token()
        if not self._classifier.is_operator_choose(tok, ("~", '`')):
            self._tokenizer.put_back(tok)
            return False
        if tok.text == '~':
            prev_tok = tok
            tok = self._tokenizer.get_next_token()
            if not self._classifier.is_operator(tok) or tok.text not in ('"', '`'):
                self._tokenizer.put_back(prev_tok)
                self._tokenizer.put_back(tok)
                return False
        target = tok.text
        while True:
            tok = self._tokenizer.get_next_token()
            if tok.style == EOF_STYLE:
                self._add_result("expecting '%s', hit end of file" % (target,), tok)
                raise SyntaxErrorEOF()
            elif self._classifier.is_operator(tok, target):
                return True

    def _parse_term(self, required=False):
        exp_num = self._parse_unary_operator()
        have_num = self._parse_number(exp_num)
        if have_num:
            return True
        elif exp_num:
            return False
        if self._parse_string():
            return True
        elif self._parse_url():
            return True
        elif self._parse_hex_color():
            return True
        elif self._parse_function_call_or_term_identifier():
            return True
        elif self._parse_variable_reference():
            return True
        elif self.language == "Less":
            if self._parse_parenthesized_expression():
                return True
            elif self._parse_escaped_string():
                return True
        if required:
            tok = self._tokenizer.get_next_token()
            self._check_tag_tok(tok, 8)
            self._add_result("expecting a value", tok)
            self._tokenizer.put_back(tok)
        return False

    def _parse_number(self, exp_num):
        tok = self._tokenizer.get_next_token()
        if not self._classifier.is_number(tok):
            if exp_num:
                self._add_result("expecting a number", tok)
                self._parser_putback_recover(tok)
            else:
                self._tokenizer.put_back(tok)
            return False
        return True

    def _parse_string(self):
        tok = self._tokenizer.get_next_token()
        if self._classifier.is_stringeol(tok):
            self._add_result("missing string close-quote", tok)
        elif not self._classifier.is_string(tok):
            self._tokenizer.put_back(tok)
            return False
        return True

    def _parse_term_identifier(self):
        required = False
        prev_tok = None
        while True:
            tok = self._tokenizer.get_next_token()
            if not (self._classifier.is_value(tok) and self._lex_identifier(tok)):
                if required:
                    self._add_result("expecting an identifier", tok)
                    # Swallow the ':' or '.' that got us here.
                    return False
                else:
                    self._tokenizer.put_back(tok)
                    return prev_tok is not None
            prev_tok = tok
            tok = self._tokenizer.get_next_token()
            if not (self._classifier.is_operator(tok)
                    and tok.text in (":", ".")): # Microsoft additions
                self._tokenizer.put_back(tok)
                return prev_tok
            op_tok = tok
            required = True

    def _parse_identifier(self):
        tok = self._tokenizer.get_next_token()
        if not (self._classifier.is_value(tok) and self._lex_identifier(tok)):
            self._tokenizer.put_back(tok)
            return False
        else:
            return True

    _url_re = re.compile(r'url\((.*)\)\Z')
    def _parse_url(self):
        tok = self._tokenizer.get_next_token()
        if self._classifier.is_value(tok):
            if self._url_re.match(tok.text):
                return True
            if tok.text == "url(":
                # Verify that the actual URL is a string
                if not self._parse_string():
                    tok = self._tokenizer.get_next_token()
                    self._add_result("expecting a quoted URL", tok)
                    self._parser_putback_recover(tok)
                tok = self._tokenizer.get_next_token()
                if not (self._classifier.is_operator(tok, ")")
                        or (self._classifier.is_value(tok) and tok.text == ')')):
                    self._add_result("expecting ')'", tok)
                    self._parser_putback_recover(tok)
                else:
                    return True
        self._tokenizer.put_back(tok)
        return False

    _hex_color_re = re.compile(r'#(?:[\da-fA-F]{3}){1,2}\Z')
    def _parse_hex_color(self):
        tok = self._tokenizer.get_next_token()
        if (self._classifier.is_value(tok)
            and self._hex_color_re.match(tok.text)):
            return True
        elif self.language != "CSS" and self._classifier.is_operator(tok, "#"):
            new_tok = self._tokenizer.get_next_token()
            if self._hex_color_re.match("#" + new_tok.text):
                return True
            self._tokenizer.put_back(tok)
            self._tokenizer.put_back(new_tok)
        else:
            self._tokenizer.put_back(tok)
        return False

    def _parse_function_call_or_term_identifier(self):
        res = self._parse_term_identifier()
        if not res:
            return False
        tok = self._tokenizer.get_next_token()
        if not self._classifier.is_operator(tok, "("):
            self._tokenizer.put_back(tok)
            return True
        self._parse_expression() # Includes commas
        self._parse_required_operator(")")
        return True

    def _parse_variable_reference(self):
        tok = self._tokenizer.get_next_token()
        if self._classifier.is_operator(tok, "@") and self.language == "Less":
            tok = self._tokenizer.get_next_token()
            # Allow multiple '@' signs
            while self._classifier.is_operator(tok, "@"):
                tok = self._tokenizer.get_next_token()
            if not (self._classifier.is_attribute(tok)
                    or self._classifier.is_identifier(tok)
                    or self._classifier.is_directive(tok)):
                self._add_result("expecting an identifier", tok)
            return True
        elif self._is_scss_variable(tok):
            return True
        self._tokenizer.put_back(tok)
        return False

    def _parse_priority(self):
        tok = self._tokenizer.get_next_token()
        if self._classifier.is_important(tok, "!important"):
            return
        elif not self._classifier.is_important(tok, "!"):
            self._tokenizer.put_back(tok)
        else:
            tok = self._tokenizer.get_next_token()
            if not self._classifier.is_important(tok, "important"):
                self._add_result("expecting '!important',", tok)
                self._parser_putback_recover(tok)

    def _parse_identifier_list(self, classifier, separator):
        while True:
            tok = self._tokenizer.get_next_token()
            self._check_tag_tok(tok, 9)
            if not self._classifier.is_operator(tok, separator):
                self._tokenizer.put_back(tok)
                break
            tok = self._tokenizer.get_next_token()
            if not (classifier(tok) and self._lex_identifier(tok)):
                self._add_result("expecting an identifier", tok)
                return self._parser_putback_recover(tok)

    def _parse_top_level(self):
        self._region = self._PARSE_REGION_AT_START
        do_declarations_this_time = False # for recovery
        while True:
            if not do_declarations_this_time:
                tok = self._tokenizer.get_next_token()
                if tok is None:
                    log.error("tok is None")
                    break
                if tok.style == EOF_STYLE:
                    return
                self._check_tag_tok(tok, 10)
            try:
                if do_declarations_this_time:
                    do_declarations_this_time = False
                    self._parse_declarations()
                if self._classifier.is_operator(tok, "@"):
                    self._parse_directive(tok)
                elif self._is_scss_variable(tok):
                    self._parse_assignment()
                else:
                    self._tokenizer.put_back(tok)
                    self._region = self._PARSE_REGION_SAW_OTHER
                    self._parse_ruleset()
            except SyntaxErrorEOF:
                break
            except SyntaxError:
                tok = self._recover(allowEOF=True, opTokens=("{", "}", "@"))
                if tok.style == EOF_STYLE:
                    return
                if self._classifier.is_operator(tok, "{"):
                    self._tokenizer.put_back(tok)
                    # slightly convoluted way of running code in the same try/except block
                    do_declarations_this_time = True
                elif self._classifier.is_operator(tok, "@"):
                    self._tokenizer.put_back(tok)
                # Otherwise consume the "}" and continue

    _identifier_lex_re = re.compile(r'(?:[a-zA-Z_\-\x80-\xff]|\\[^\r\n\f0-9a-fA-F])(?:[\w_\-\x80-\xff]|\\[^\r\n\f0-9a-fA-F])*$')
    def _lex_identifier(self, tok):
        return self._identifier_lex_re.match(tok.text)

    
    def _is_scss_variable(self, tok):
        if self.language != "SCSS":
            return False
        return (self._classifier.is_identifier(tok)
                and tok.text[0] == "$")

    _check_tag_tok_count = 0
    def _check_tag_tok(self, tok, loop_id):
        tag = "_check_loop_%d" % (loop_id,)
        if not hasattr(tok, tag):
            self._check_tag_tok_count += 1
            setattr(tok, tag, self._check_tag_tok_count)
        elif getattr(tok, tag) == self._check_tag_tok_count:
            raise Exception("Stuck in a loop with tok %s, tag %d" % (tok.dump_ret(), loop_id))

class CSSLinter(object):
    def lint(self, text, language="CSS"):
        self._parser = _CSSParser(language)
        results = self._parser.parse(text)
        return results