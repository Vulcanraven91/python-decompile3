#  Copyright (c) 2019 by Rocky Bernstein
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
All the crazy things we have to do to handle Python functions
"""
from xdis.code import iscode, code_has_star_arg, code_has_star_star_arg
from decompyle3.scanner import Code
from decompyle3.parsers.treenode import SyntaxTree
from decompyle3.semantics.parser_error import ParserError
from decompyle3.parser import ParserError as ParserError2
from decompyle3.semantics.helper import (
    print_docstring,
    find_all_globals,
    find_globals_and_nonlocals,
    find_none,
)

from itertools import zip_longest

from decompyle3.show import maybe_show_tree_param_default

# FIXME: DRY the below code...

def make_function2(self, node, is_lambda, nested=1, code_node=None):
    """
    Dump function defintion, doc string, and function body.
    This code is specialied for Python 2.
    """

    # FIXME: call make_function3 if we are self.version >= 3.0
    # and then simplify the below.

    def build_param(ast, name, default):
        """build parameters:
            - handle defaults
            - handle format tuple parameters
        """
        # if formal parameter is a tuple, the paramater name
        # starts with a dot (eg. '.1', '.2')
        if name.startswith("."):
            # replace the name with the tuple-string
            name = self.get_tuple_parameter(ast, name)
            pass

        if default:
            value = self.traverse(default, indent="")
            maybe_show_tree_param_default(self.showast, name, value)
            result = "%s=%s" % (name, value)
            if result[-2:] == "= ":  # default was 'LOAD_CONST None'
                result += "None"
            return result
        else:
            return name

    # MAKE_FUNCTION_... or MAKE_CLOSURE_...
    assert node[-1].kind.startswith("MAKE_")

    args_node = node[-1]
    if isinstance(args_node.attr, tuple):
        # positional args are after kwargs
        defparams = node[1 : args_node.attr[0] + 1]
        pos_args, kw_args, annotate_argc = args_node.attr
    else:
        defparams = node[: args_node.attr]
        kw_args = 0
        pass

    lambda_index = None

    if lambda_index and is_lambda and iscode(node[lambda_index].attr):
        assert node[lambda_index].kind == "LOAD_LAMBDA"
        code = node[lambda_index].attr
    else:
        code = code_node.attr

    assert iscode(code)
    code = Code(code, self.scanner, self.currentclass)

    # add defaults values to parameter names
    argc = code.co_argcount
    paramnames = list(code.co_varnames[:argc])

    # defaults are for last n parameters, thus reverse
    paramnames.reverse()
    defparams.reverse()

    try:
        ast = self.build_ast(
            code._tokens,
            code._customize,
            is_lambda=is_lambda,
            noneInNames=("None" in code.co_names),
        )
    except (ParserError, ParserError2) as p:
        self.write(str(p))
        if not self.tolerate_errors:
            self.ERROR = p
        return

    kw_pairs = 0
    indent = self.indent

    # build parameters
    params = [
        build_param(ast, name, default)
        for name, default in zip_longest(paramnames, defparams, fillvalue=None)
    ]
    params.reverse()  # back to correct order

    if code_has_star_arg(code):
        params.append("*%s" % code.co_varnames[argc])
        argc += 1

    # dump parameter list (with default values)
    if is_lambda:
        self.write("lambda ", ", ".join(params))
        # If the last statement is None (which is the
        # same thing as "return None" in a lambda) and the
        # next to last statement is a "yield". Then we want to
        # drop the (return) None since that was just put there
        # to have something to after the yield finishes.
        # FIXME: this is a bit hoaky and not general
        if (
            len(ast) > 1
            and self.traverse(ast[-1]) == "None"
            and self.traverse(ast[-2]).strip().startswith("yield")
        ):
            del ast[-1]
            # Now pick out the expr part of the last statement
            ast_expr = ast[-1]
            while ast_expr.kind != "expr":
                ast_expr = ast_expr[0]
            ast[-1] = ast_expr
            pass
    else:
        self.write("(", ", ".join(params))

    if kw_args > 0:
        if not (4 & code.co_flags):
            if argc > 0:
                self.write(", *, ")
            else:
                self.write("*, ")
            pass
        else:
            self.write(", ")

        for n in node:
            if n == "pos_arg":
                continue
            else:
                self.preorder(n)
            break
        pass

    if code_has_star_star_arg(code):
        if argc > 0:
            self.write(", ")
        self.write("**%s" % code.co_varnames[argc + kw_pairs])

    if is_lambda:
        self.write(": ")
    else:
        self.println("):")

    if (
        len(code.co_consts) > 0 and code.co_consts[0] is not None and not is_lambda
    ):  # ugly
        # docstring exists, dump it
        print_docstring(self, indent, code.co_consts[0])

    code._tokens = None  # save memory
    if not is_lambda:
        assert ast == "stmts"

    all_globals = find_all_globals(ast, set())

    globals, nonlocals = find_globals_and_nonlocals(
        ast, set(), set(), code, self.version
    )

    # Python 2 doesn't support the "nonlocal" statement
    assert self.version >= 3.0 or not nonlocals

    for g in sorted((all_globals & self.mod_globs) | globals):
        self.println(self.indent, "global ", g)
    self.mod_globs -= all_globals
    has_none = "None" in code.co_names
    rn = has_none and not find_none(ast)
    self.gen_source(
        ast, code.co_name, code._customize, is_lambda=is_lambda, returnNone=rn
    )
    code._tokens = None
    code._customize = None  # save memory


def make_function3(self, node, is_lambda, nested=1, code_node=None):
    """Dump function definition, doc string, and function body in
      Python version 3.0 and above
    """

    # For Python 3.3, the evaluation stack in MAKE_FUNCTION is:

    # * default argument objects in positional order
    # * pairs of name and default argument, with the name just below
    #   the object on the stack, for keyword-only parameters
    # * parameter annotation objects
    # * a tuple listing the parameter names for the annotations
    #   (only if there are ony annotation objects)
    # * the code associated with the function (at TOS1)
    # * the qualified name of the function (at TOS)

    # For Python 3.0 .. 3.2 the evaluation stack is:
    # The function object is defined to have argc default parameters,
    # which are found below TOS.
    # * first come positional args in the order they are given in the source,
    # * next come the keyword args in the order they given in the source,
    # * finally is the code associated with the function (at TOS)
    #
    # Note: There is no qualified name at TOS

    # MAKE_CLOSURE adds an additional closure slot

    # In Python 3.6 stack entries change again. I understand
    # 3.7 changes some of those changes. Yes, it is hard to follow
    # and I am sure I haven't been able to keep up.

    # Thank you, Python.

    def build_param(ast, name, default, annotation=None):
        """build parameters:
            - handle defaults
            - handle format tuple parameters
        """
        value = default
        maybe_show_tree_param_default(self.showast, name, value)
        if annotation:
            result = "%s: %s=%s" % (name, annotation, value)
        else:
            result = "%s=%s" % (name, value)

        # The below can probably be removed. This is probably
        # a holdover from days when LOAD_CONST erroneously
        # didn't handle LOAD_CONST None properly
        if result[-2:] == "= ":  # default was 'LOAD_CONST None'
            result += "None"

        return result

    # MAKE_FUNCTION_... or MAKE_CLOSURE_...
    assert node[-1].kind.startswith("MAKE_")

    # Python 3.3+ adds a qualified name at TOS (-1)
    # moving down the LOAD_LAMBDA instruction
    lambda_index = -3

    args_node = node[-1]

    annotate_dict = {}

    # Get a list of tree nodes that constitute the values for the "default
    # parameters"; these are default values that appear before any *, and are
    # not to be confused with keyword parameters which may appear after *.
    args_attr = args_node.attr

    if isinstance(args_attr, tuple) or isinstance(args_attr, list):
        if len(args_attr) == 3:
            pos_args, kw_args, annotate_argc = args_attr
        else:
            pos_args, kw_args, annotate_argc, closure = args_attr

            i = -4
            kw_pairs = 0
            if closure:
                # FIXME: fill in
                i -= 1
            if annotate_argc:
                # Turn into subroutine and DRY with other use
                annotate_node = node[i]
                if annotate_node == "expr":
                    annotate_node = annotate_node[0]
                    annotate_name_node = annotate_node[-1]
                    if annotate_node == "dict" and annotate_name_node.kind.startswith(
                        "BUILD_CONST_KEY_MAP"
                    ):
                        types = [
                            self.traverse(n, indent="") for n in annotate_node[:-2]
                        ]
                        names = annotate_node[-2].attr
                        l = len(types)
                        assert l == len(names)
                        for i in range(l):
                            annotate_dict[names[i]] = types[i]
                        pass
                    pass
                i -= 1
            if kw_args:
                kw_node = node[i]
                if kw_node == "expr":
                    kw_node = kw_node[0]
                if kw_node == "dict":
                    kw_pairs = kw_node[-1].attr

        defparams = []
        # FIXME: DRY with code below
        default, kw_args, annotate_argc = args_node.attr[0:3]
        if default:
            expr_node = node[0]
            if node[0] == "pos_arg":
                expr_node = expr_node[0]
            assert expr_node == "expr", "expecting mkfunc default node to be an expr"
            if expr_node[0] == "LOAD_CONST" and isinstance(expr_node[0].attr, tuple):
                defparams = [repr(a) for a in expr_node[0].attr]
            elif expr_node[0] in frozenset(("list", "tuple", "dict", "set")):
                defparams = [self.traverse(n, indent="") for n in expr_node[0][:-1]]
        else:
            defparams = []
        pass
    else:
        default, kw_args, annotate, closure = args_node.attr
        if default:
            expr_node = node[0]
            if node[0] == "pos_arg":
                expr_node = expr_node[0]
            assert expr_node == "expr", "expecting mkfunc default node to be an expr"
            if expr_node[0] == "LOAD_CONST" and isinstance(expr_node[0].attr, tuple):
                defparams = [repr(a) for a in expr_node[0].attr]
            elif expr_node[0] in frozenset(("list", "tuple", "dict", "set")):
                defparams = [self.traverse(n, indent="") for n in expr_node[0][:-1]]
        else:
            defparams = []

        i = -4
        kw_pairs = 0
        if closure:
            # FIXME: fill in
            # annotate = node[i]
            i -= 1
        if annotate_argc:
            # Turn into subroutine and DRY with other use
            annotate_node = node[i]
            if annotate_node == "expr":
                annotate_node = annotate_node[0]
                annotate_name_node = annotate_node[-1]
                if annotate_node == "dict" and annotate_name_node.kind.startswith(
                    "BUILD_CONST_KEY_MAP"
                ):
                    types = [self.traverse(n, indent="") for n in annotate_node[:-2]]
                    names = annotate_node[-2].attr
                    l = len(types)
                    assert l == len(names)
                    for i in range(l):
                        annotate_dict[names[i]] = types[i]
                    pass
                pass
            i -= 1
        if kw_args:
            kw_node = node[i]
            if kw_node == "expr":
                kw_node = kw_node[0]
            if kw_node == "dict":
                kw_pairs = kw_node[-1].attr
        pass

    if lambda_index and is_lambda and iscode(node[lambda_index].attr):
        assert node[lambda_index].kind == "LOAD_LAMBDA"
        code = node[lambda_index].attr
    else:
        code = code_node.attr

    assert iscode(code)
    scanner_code = Code(code, self.scanner, self.currentclass)

    # add defaults values to parameter names
    argc = code.co_argcount
    kwonlyargcount = code.co_kwonlyargcount

    paramnames = list(scanner_code.co_varnames[:argc])
    kwargs = list(scanner_code.co_varnames[argc : argc + kwonlyargcount])

    # defaults are for last n parameters, thus reverse
    paramnames.reverse()
    defparams.reverse()

    try:
        ast = self.build_ast(
            scanner_code._tokens,
            scanner_code._customize,
            is_lambda=is_lambda,
            noneInNames=("None" in code.co_names),
        )
    except (ParserError, ParserError2) as p:
        self.write(str(p))
        if not self.tolerate_errors:
            self.ERROR = p
        return

    i = len(paramnames) - len(defparams)

    # build parameters
    params = []
    if defparams:
        for i, defparam in enumerate(defparams):
            params.append(
                build_param(
                    ast, paramnames[i], defparam, annotate_dict.get(paramnames[i])
                )
            )

        for param in paramnames[i + 1 :]:
            if param in annotate_dict:
                params.append("%s: %s" % (param, annotate_dict[param]))
            else:
                params.append(param)
    else:
        for param in paramnames:
            if param in annotate_dict:
                params.append("%s: %s" % (param, annotate_dict[param]))
            else:
                params.append(param)

    params.reverse()  # back to correct order

    if code_has_star_arg(code):
        star_arg = code.co_varnames[argc + kwonlyargcount]
        if star_arg in annotate_dict:
            params.append("*%s: %s" % (star_arg, annotate_dict[star_arg]))
        else:
            params.append("*%s" % star_arg)
        argc += 1

    # dump parameter list (with default values)
    if is_lambda:
        self.write("lambda ", ", ".join(params))
        # If the last statement is None (which is the
        # same thing as "return None" in a lambda) and the
        # next to last statement is a "yield". Then we want to
        # drop the (return) None since that was just put there
        # to have something to after the yield finishes.
        # FIXME: this is a bit hoaky and not general
        if (
            len(ast) > 1
            and self.traverse(ast[-1]) == "None"
            and self.traverse(ast[-2]).strip().startswith("yield")
        ):
            del ast[-1]
            # Now pick out the expr part of the last statement
            ast_expr = ast[-1]
            while ast_expr.kind != "expr":
                ast_expr = ast_expr[0]
            ast[-1] = ast_expr
            pass
    else:
        self.write("(", ", ".join(params))
    # self.println(indent, '#flags:\t', int(code.co_flags))

    ends_in_comma = False
    if kwonlyargcount > 0:
        if not (4 & code.co_flags):
            if argc > 0:
                self.write(", *, ")
            else:
                self.write("*, ")
            pass
            ends_in_comma = True
        else:
            if argc > 0:
                self.write(", ")
                ends_in_comma = True

        ann_dict = kw_dict = default_tup = None
        fn_bits = node[-1].attr
        index = -4  # Skip over:
        #  MAKE_FUNCTION,
        #  LOAD_CONST qualified name,
        #  LOAD_CONST code object
        if fn_bits[-1]:
            index -= 1
        if fn_bits[-2]:
            ann_dict = node[index]
            index -= 1
        if fn_bits[-3]:
            kw_dict = node[index]
            index -= 1
        if fn_bits[-4]:
            default_tup = node[index]

        if kw_dict == "expr":
            kw_dict = kw_dict[0]

        kw_args = [None] * kwonlyargcount

        # FIXME: handle free_tup, ann_dict, and default_tup
        if kw_dict:
            assert kw_dict == "dict"
            defaults = [self.traverse(n, indent="") for n in kw_dict[:-2]]
            names = eval(self.traverse(kw_dict[-2]))
            assert len(defaults) == len(names)
            sep = ""
            # FIXME: possibly handle line breaks
            for i, n in enumerate(names):
                idx = kwargs.index(n)
                if annotate_dict and n in annotate_dict:
                    t = "%s: %s=%s" % (n, annotate_dict[n], defaults[i])
                else:
                    t = "%s=%s" % (n, defaults[i])
                kw_args[idx] = t
                pass
            pass
        # handle others
        other_kw = [c == None for c in kw_args]

        for i, flag in enumerate(other_kw):
            if flag:
                n = kwargs[i]
                if n in annotate_dict:
                    kw_args[i] = "%s: %s" % (n, annotate_dict[n])
                else:
                    kw_args[i] = "%s" % n

        self.write(", ".join(kw_args))
        ends_in_comma = False
        pass
    else:
        if argc == 0:
            ends_in_comma = True

    if code_has_star_star_arg(code):
        if not ends_in_comma:
            self.write(", ")
        star_star_arg = code.co_varnames[argc + kwonlyargcount]
        if annotate_dict and star_star_arg in annotate_dict:
            self.write("**%s: %s" % (star_star_arg, annotate_dict[star_star_arg]))
        else:
            self.write("**%s" % star_star_arg)

    if is_lambda:
        self.write(": ")
    else:
        self.write(")")
        if annotate_dict and "return" in annotate_dict:
            self.write(" -> %s" % annotate_dict["return"])
        self.println(":")

    if (
        len(code.co_consts) > 0 and code.co_consts[0] is not None and not is_lambda
    ):  # ugly
        # docstring exists, dump it
        print_docstring(self, self.indent, code.co_consts[0])

    scanner_code._tokens = None  # save memory
    assert ast == "stmts"

    all_globals = find_all_globals(ast, set())
    globals, nonlocals = find_globals_and_nonlocals(
        ast, set(), set(), code, self.version
    )

    for g in sorted((all_globals & self.mod_globs) | globals):
        self.println(self.indent, "global ", g)

    for nl in sorted(nonlocals):
        self.println(self.indent, "nonlocal ", nl)

    self.mod_globs -= all_globals
    has_none = "None" in code.co_names
    rn = has_none and not find_none(ast)
    self.gen_source(
        ast, code.co_name, scanner_code._customize, is_lambda=is_lambda, returnNone=rn
    )
    scanner_code._tokens = None
    scanner_code._customize = None  # save memory