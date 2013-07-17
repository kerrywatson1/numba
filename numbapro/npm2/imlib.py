from contextlib import contextmanager
import operator
import llvm.core as lc
from . import typesets, types

class ImpLib(object):
    def __init__(self, funclib):
        self.funclib = funclib
        self.implib = {}

    def define(self, imp):
        defn = self.funclib.lookup(imp.funcobj, imp.args)
        if defn is None:
            from pprint import pprint
            pprint(self.implib)
            msg = 'no matching definition for %s(%s)'
            raise TypeError(msg % (imp.funcobj, ', '.join(map(str, imp.args))))
        if defn.return_type != imp.return_type:
            msg = ('return-type mismatch for implementation; '
                   'expect %s but got %s')
            raise TypeError(msg % (defn.return_type, imp.return_type))
        self.implib[defn] = imp

    def get(self, funcdef):
        return self.implib[funcdef]

    def populate_builtin(self):
        populate_builtin_impl(self)

class Imp(object):
    def __init__(self, impl, funcobj, args, return_type):
        self.impl = impl
        self.funcobj = funcobj
        self.args = args
        self.return_type = return_type

    def __call__(self, builder, args):
        return self.impl(builder, args)


    def __repr__(self):
        return '<Impl %s %s -> %s >' % (self.funcobj, self.args,
                                        self.return_type)

# binary add

def imp_add_integer(builder, args):
    a, b = args
    return builder.add(a, b)

def imp_add_float(builder, args):
    a, b = args
    return builder.fadd(a, b)

def imp_add_complex(dtype):
    def imp(builder, args):
        a, b = args

        a_real, a_imag = dtype.desc.llvm_unpack(builder, a)
        b_real, b_imag = dtype.desc.llvm_unpack(builder, b)

        c_real = imp_add_float(builder, (a_real, b_real))
        c_imag = imp_add_float(builder, (a_imag, b_imag))

        return dtype.desc.llvm_pack(builder, c_real, c_imag)
    return imp

# binary sub

def imp_sub_integer(builder, args):
    a, b = args
    return builder.sub(a, b)

def imp_sub_float(builder, args):
    a, b = args
    return builder.fsub(a, b)

def imp_sub_complex(dtype):
    def imp(builder, args):
        a, b = args

        a_real, a_imag = dtype.desc.llvm_unpack(builder, a)
        b_real, b_imag = dtype.desc.llvm_unpack(builder, b)

        c_real = imp_sub_float(builder, (a_real, b_real))
        c_imag = imp_sub_float(builder, (a_imag, b_imag))

        return dtype.desc.llvm_pack(builder, c_real, c_imag)
    return imp


# binary mul

def imp_mul_integer(builder, args):
    a, b = args
    return builder.mul(a, b)

def imp_mul_float(builder, args):
    a, b = args
    return builder.fmul(a, b)

def imp_mul_complex(dtype):
    '''
    x y = (a c - b d) + i (a d + b c)
    '''
    def imp(builder, args):
        x, y = args

        a, b = dtype.desc.llvm_unpack(builder, x)
        c, d = dtype.desc.llvm_unpack(builder, y)

        ac = imp_mul_float(builder, (a, c))
        bd = imp_mul_float(builder, (b, d))
        ad = imp_mul_float(builder, (a, d))
        bc = imp_mul_float(builder, (b, c))

        real = imp_sub_float(builder, (ac, bd))
        imag = imp_add_float(builder, (ad, bc))

        return dtype.desc.llvm_pack(builder, real, imag)
    return imp

# binary floordiv

def imp_floordiv_signed(builder, args):
    a, b = args
    return builder.sdiv(a, b)

def imp_floordiv_unsigned(builder, args):
    a, b = args
    return builder.udiv(a, b)

def imp_floordiv_float(intty):
    def imp(builder, args):
        a, b = args
        return builder.fptosi(builder.fdiv(a, b),
                              lc.Type.int(intty.desc.bitwidth))
    return imp

# binary truediv

def imp_truediv_float(builder, args):
    a, b = args
    return builder.fdiv(a, b)

# binary mod

def imp_mod_signed(builder, args):
    a, b = args
    return builder.srem(a, b)

def imp_mod_unsigned(builder, args):
    a, b = args
    return builder.urem(a, b)

def imp_mod_float(builder, args):
    a, b = args
    return builder.frem(a, b)

# binary lshift

def imp_lshift_integer(builder, args):
    a, b = args
    return builder.shl(a, b)

# binary rshift

def imp_rshift_signed(builder, args):
    a, b = args
    return builder.ashr(a, b)

def imp_rshift_unsigned(builder, args):
    a, b = args
    return builder.lshr(a, b)

# binary and

def imp_and_integer(builder, args):
    a, b = args
    return builder.and_(a, b)

# binary or

def imp_or_integer(builder, args):
    a, b = args
    return builder.or_(a, b)

# binary xor

def imp_xor_integer(builder, args):
    a, b = args
    return builder.xor(a, b)

# unary negate

def imp_neg_signed(ty):
    def imp(builder, args):
        x, = args
        zero = ty.llvm_const(builder, 0)
        return imp_sub_integer(builder, (zero, x))
    return imp

def imp_neg_float(ty):
    def imp(builder, args):
        x, = args
        zero = ty.llvm_const(builder, 0)
        return imp_sub_float(builder, (zero, x))
    return imp

def imp_neg_complex(ty):
    def imp(builder, args):
        x, = args
        zero = ty.llvm_const(builder, 0)
        return imp_sub_complex(ty)(builder, (zero, x))
    return imp

# unary invert

def imp_invert_integer(ty):
    def imp(builder, args):
        x, = args
        ones = lc.Constant.all_ones(ty.llvm_as_value())
        return builder.xor(x, ones)
    return imp

# bool eq

def imp_eq_signed(builder, args):
    a, b = args
    return builder.icmp(lc.ICMP_EQ, a, b)

# bool comparisions

def imp_cmp_signed(cmp, ty):
    CMP = {
        operator.gt: lc.ICMP_SGT,
        operator.lt: lc.ICMP_SLT,
        operator.ge: lc.ICMP_SGE,
        operator.le: lc.ICMP_SLE,
        operator.eq: lc.ICMP_EQ,
        operator.ne: lc.ICMP_NE,
    }
    def imp(builder, args):
        a, b = args
        return builder.icmp(CMP[cmp], a, b)
    return imp

def imp_cmp_unsigned(cmp, ty):
    CMP = {
        operator.gt: lc.ICMP_UGT,
        operator.lt: lc.ICMP_ULT,
        operator.ge: lc.ICMP_UGE,
        operator.le: lc.ICMP_ULE,
        operator.eq: lc.ICMP_EQ,
        operator.ne: lc.ICMP_NE,
    }
    def imp(builder, args):
        a, b = args
        return builder.icmp(CMP[cmp], a, b)
    return imp

def imp_cmp_float(cmp, ty):
    CMP = {
        operator.gt: lc.FCMP_OGT,
        operator.lt: lc.FCMP_OLT,
        operator.ge: lc.FCMP_OGE,
        operator.le: lc.FCMP_OLE,
        operator.eq: lc.FCMP_OEQ,
        operator.ne: lc.FCMP_ONE,
    }
    def imp(builder, args):
        a, b = args
        return builder.fcmp(CMP[cmp], a, b)
    return imp

def imp_cmp_complex(cmp, ty):
    def imp(builder, args):
        a, b = args
        areal, aimag = ty.desc.llvm_unpack(a)
        breal, bimag = ty.desc.llvm_unpack(b)
        cmptor = imp_cmp_float(cmp, ty.desc.element)
        c = cmptor(areal, breal)
        d = cmptor(aimag, bimag)
        return builder.and_(c, d)
    return imp

# range

def make_range_obj(builder, start, stop, step):
    rangetype = types.range_type.llvm_as_value()
    rangeobj = lc.Constant.undef(rangetype)
    
    rangeobj = builder.insert_value(rangeobj, start, 0)
    rangeobj = builder.insert_value(rangeobj, stop, 1)
    rangeobj = builder.insert_value(rangeobj, step, 2)
    return rangeobj

def imp_range_1(builder, args):
    assert len(args) == 1
    (stop,) = args
    start = types.intp.llvm_const(builder, 0)
    step = types.intp.llvm_const(builder, 1)
    return make_range_obj(builder, start, stop, step)

def imp_range_2(builder, args):
    assert len(args) == 2
    start, stop = args
    step = types.intp.llvm_const(builder, 1)
    return make_range_obj(builder, start, stop, step)

def imp_range_3(builder, args):
    assert len(args) == 3
    start, stop, step = args
    return make_range_obj(builder, start, stop, step)

def imp_range_iter(builder, args):
    obj, = args
    with goto_entry_block(builder):
        # allocate at the beginning
        # assuming a range object must be used statically
        ptr = builder.alloca(obj.type)
    builder.store(obj, ptr)
    return ptr

def imp_range_valid(builder, args):
    ptr, = args
    idx0 = types.int32.llvm_const(builder, 0)
    idx1 = types.int32.llvm_const(builder, 1)
    idx2 = types.int32.llvm_const(builder, 2)
    start = builder.load(builder.gep(ptr, [idx0, idx0]))
    stop = builder.load(builder.gep(ptr, [idx0, idx1]))
    step = builder.load(builder.gep(ptr, [idx0, idx2]))

    zero = types.intp.llvm_const(builder, 0)
    positive = builder.icmp(lc.ICMP_SGE, step, zero)
    posok = builder.icmp(lc.ICMP_SLT, start, stop)
    negok = builder.icmp(lc.ICMP_SGT, start, stop)
    
    return builder.select(positive, posok, negok)

def imp_range_next(builder, args):
    ptr, = args
    idx0 = types.int32.llvm_const(builder, 0)
    idx2 = types.int32.llvm_const(builder, 2)
    startptr = builder.gep(ptr, [idx0, idx0])
    start = builder.load(startptr)
    step = builder.load(builder.gep(ptr, [idx0, idx2]))
    next = builder.add(start, step)
    builder.store(next, startptr)
    return start

#----------------------------------------------------------------------------
# utils

def bool_op_imp(funcobj, imp, typeset):
    return [Imp(imp(funcobj, ty), funcobj,
                args=(ty, ty),
                return_type=types.boolean)
            for ty in typeset]

def binary_op_imp(funcobj, imp, typeset):
    return [Imp(imp, funcobj, args=(ty, ty), return_type=ty)
            for ty in typeset]

def unary_op_imp(funcobj, imp, typeset):
    return [Imp(imp(ty), funcobj, args=(ty,), return_type=ty)
            for ty in typeset]

def floordiv_imp(funcobj, imp, ty, ret):
    return [Imp(imp(ret), funcobj, args=(ty, ty), return_type=ret)]


def populate_builtin_impl(implib):
    imps = []

    # binary add
    imps += binary_op_imp(operator.add, imp_add_integer, typesets.integer_set)
    imps += binary_op_imp(operator.add, imp_add_float, typesets.float_set)
    imps += binary_op_imp(operator.add, imp_add_complex(types.complex64),
                          [types.complex64])
    imps += binary_op_imp(operator.add, imp_add_complex(types.complex128),
                          [types.complex128])

    # binary sub
    imps += binary_op_imp(operator.sub, imp_sub_integer, typesets.integer_set)
    imps += binary_op_imp(operator.sub, imp_sub_float, typesets.float_set)
    imps += binary_op_imp(operator.sub, imp_sub_complex(types.complex64),
                          [types.complex64])
    imps += binary_op_imp(operator.sub, imp_sub_complex(types.complex128),
                          [types.complex128])
    
    # binary mul
    imps += binary_op_imp(operator.mul, imp_mul_integer, typesets.integer_set)
    imps += binary_op_imp(operator.mul, imp_mul_float, typesets.float_set)
    imps += binary_op_imp(operator.mul, imp_mul_complex(types.complex64),
                          [types.complex64])
    imps += binary_op_imp(operator.mul, imp_mul_complex(types.complex128),
                          [types.complex128])

    # binary floordiv
    imps += binary_op_imp(operator.floordiv, imp_floordiv_signed,
                          typesets.signed_set)
    imps += binary_op_imp(operator.floordiv, imp_floordiv_unsigned,
                          typesets.unsigned_set)
    imps += floordiv_imp(operator.floordiv, imp_floordiv_float,
                         types.float32, types.int32)
    imps += floordiv_imp(operator.floordiv, imp_floordiv_float,
                         types.float64, types.int64)

    # binary truediv
    imps += binary_op_imp(operator.truediv, imp_truediv_float,
                          typesets.float_set)

    # binary mod
    imps += binary_op_imp(operator.mod, imp_mod_signed, typesets.signed_set)
    imps += binary_op_imp(operator.mod, imp_mod_unsigned, typesets.unsigned_set)
    imps += binary_op_imp(operator.mod, imp_mod_float, typesets.float_set)

    # binary lshift
    imps += binary_op_imp(operator.lshift, imp_lshift_integer,
                          typesets.integer_set)
    # binary rshift
    imps += binary_op_imp(operator.rshift, imp_rshift_signed,
                          typesets.signed_set)
    imps += binary_op_imp(operator.rshift, imp_rshift_unsigned,
                          typesets.unsigned_set)
    
    # binary and
    imps += binary_op_imp(operator.and_, imp_and_integer,
                          typesets.integer_set)

    # binary or
    imps += binary_op_imp(operator.or_, imp_or_integer,
                          typesets.integer_set)

    # binary xor
    imps += binary_op_imp(operator.xor, imp_xor_integer,
                          typesets.integer_set)

    # bool gt
    for cmp in [operator.gt, operator.ge, operator.lt, operator.le,
                operator.eq, operator.ne]:
#    for cmp in [operator.gt]:
        imps += bool_op_imp(cmp, imp_cmp_signed,   typesets.signed_set)
        imps += bool_op_imp(cmp, imp_cmp_unsigned, typesets.unsigned_set)
        imps += bool_op_imp(cmp, imp_cmp_float,    typesets.float_set)
        imps += bool_op_imp(cmp, imp_cmp_complex,  typesets.complex_set)

    # unary arith negate
    imps += unary_op_imp(operator.neg, imp_neg_signed, typesets.signed_set)
    imps += unary_op_imp(operator.neg, imp_neg_float, typesets.float_set)
    imps += unary_op_imp(operator.neg, imp_neg_complex, typesets.complex_set)

    # unary logical negate
    imps += unary_op_imp(operator.invert, imp_invert_integer,
                         typesets.integer_set)

    # range
    for rangeobj in [range, xrange]:
        imps += [Imp(imp_range_1, rangeobj,
                     args=(types.intp,),
                     return_type=types.range_type)]

        imps += [Imp(imp_range_2, rangeobj,
                     args=(types.intp, types.intp),
                     return_type=types.range_type)]

        imps += [Imp(imp_range_3, rangeobj,
                     args=(types.intp, types.intp, types.intp),
                     return_type=types.range_type)]

    imps += [Imp(imp_range_iter, iter,
                 args=(types.range_type,),
                 return_type=types.range_iter_type)]

    imps += [Imp(imp_range_valid, 'itervalid',
                 args=(types.range_iter_type,),
                 return_type=types.boolean)]

    imps += [Imp(imp_range_next, 'iternext',
                 args=(types.range_iter_type,),
                 return_type=types.intp)]



    for imp in imps:
        implib.define(imp)


#------------------------------------------------------------------------------
# utils

@contextmanager
def goto_entry_block(builder):
    old = builder.basic_block
    entry = builder.basic_block.function.basic_blocks[0]
    builder.position_at_beginning(entry)
    yield
    builder.position_at_end(old)
