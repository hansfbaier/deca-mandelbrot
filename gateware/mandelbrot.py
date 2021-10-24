from nmigen import *
from nmigen.build import Platform
from nmigen_library.test   import GatewareTestCase, sync_test_case

class Mandelbrot(Elaboratable):
    def __init__(self, *, bitwidth=128, fraction_bits=120, test=False):
        # Parameters
        self._bitwidth = bitwidth
        self._fraction_bits = fraction_bits
        self._test = test
        self.stages = 4

        # Inputs
        self.cx_in             = Array([Signal(signed(bitwidth), name=f"cx_{n}") for n in range(self.stages)])
        self.cy_in             = Array([Signal(signed(bitwidth), name=f"cy_{n}") for n in range(self.stages)])
        self.start_in          = Signal()
        self.max_iterations_in = Signal(32)
        self.result_read_in    = Signal()

        # Outputs
        self.busy_out          = Signal()
        self.result_ready_out  = Signal()

        self.escape_out        = Array([Signal(    name=f"escape{n}_out")     for n in range(self.stages)])
        self.iterations_out    = Array([Signal(32, name=f"iterations{n}_out") for n in range(self.stages)])

        if test:
            self.x          = Signal.like(self.cx_in[0])
            self.y          = Signal.like(self.cy_in[0])
            self.xx_plus_yy = Signal.like(self.cy_in[0])

    def elaborate(self, platform: Platform) -> Module:
        m = Module()
        bitwidth = self._bitwidth
        scale = self._fraction_bits
        test = self._test

        running   = Signal()
        iteration = Signal(32)

        # pipeline stages enable signals
        stage_enable = Signal(4)

        # which result is currently being at the last stage
        result_no = Signal(range(self.stages))

        # which pipeline stages are finished processing
        done = Array([Signal(name=f"done_{n}") for n in range(self.stages)])

        # pipeline stage 0
        x_stage_0     = Signal(signed(bitwidth))
        y_stage_0     = Signal(signed(bitwidth))
        xx_stage_0    = Signal(signed(bitwidth))

        # pipeline stage 1
        x_stage_1     = Signal(signed(bitwidth))
        y_stage_1     = Signal(signed(bitwidth))
        xx_stage_1    = Signal(signed(bitwidth))
        yy_stage_1    = Signal(signed(bitwidth))

        # pipeline stage 2
        two_xy        = Signal(signed(bitwidth))
        xx_plus_yy    = Signal(signed(bitwidth))
        xx_minus_yy   = Signal(signed(bitwidth))

        # pipeline stage 3
        x_stage_3     = Signal(signed(bitwidth))
        y_stage_3     = Signal(signed(bitwidth))
        escape        = Signal()
        maxed_out     = Signal()
        result_read   = Signal(reset=1)

        four = Signal(signed(bitwidth))

        with m.If(self.result_read_in):
            m.d.sync += [
                result_read              .eq(1),
                self.result_ready_out    .eq(0),
                Cat(self.escape_out)     .eq(0),
                Cat(self.iterations_out) .eq(0),
            ]

        m.d.comb += [
            self.busy_out.eq(running | ~result_read),
            four.eq(Const(4, signed(bitwidth)) << scale),
        ]

        # instantiate a multiplier for reuse
        # the product has one bit more than necessary
        # because we want to preserve the bit of precision
        # for the factor 2xy
        factor1           = Signal(signed(bitwidth))
        factor2           = Signal(signed(bitwidth))
        two_times_product = Signal(signed(bitwidth))

        m.d.comb += two_times_product.eq((factor1 * factor2) >> (scale - 1))

        if test:
            m.d.comb += [
                self.x.eq(x_stage_0),
                self.y.eq(y_stage_0),
                self.xx_plus_yy.eq(xx_plus_yy),
            ]

        # processing pipleline
        # here still used in a sequential manner
        # to be made fully pipelined later
        with m.If(stage_enable[0]):
            # stage 0
            m.d.comb += [
                factor1.eq(x_stage_3),
                factor2.eq(x_stage_3),
            ]
            m.d.sync += [
                xx_stage_0.eq(two_times_product >> 1),
                x_stage_0.eq(x_stage_3),
                y_stage_0.eq(y_stage_3),
            ]

        with m.If(stage_enable[1]):
            # stage 1
            m.d.comb += [
                factor1.eq(y_stage_0),
                factor2.eq(y_stage_0),
            ]
            m.d.sync += [
                yy_stage_1.eq(two_times_product >> 1),
                xx_stage_1.eq(xx_stage_0),
                x_stage_1.eq(x_stage_0),
                y_stage_1.eq(y_stage_0),
            ]

        with m.If(stage_enable[2]):
            # stage 2
            m.d.comb += [
                factor1.eq(x_stage_1),
                factor2.eq(y_stage_1),
            ]
            m.d.sync += [
                two_xy        .eq(two_times_product),
                xx_plus_yy    .eq(xx_stage_1 + yy_stage_1),
                xx_minus_yy   .eq(xx_stage_1 - yy_stage_1),
            ]

        with m.If(stage_enable[3]):
            # stage 3
            m.d.comb += [
                escape        .eq(xx_plus_yy > four),
                maxed_out     .eq(iteration >= self.max_iterations_in),
            ]
            m.d.sync += [
                x_stage_3     .eq(xx_minus_yy   + self.cx_in[result_no]),
                y_stage_3     .eq(two_xy        + self.cy_in[result_no]),
                result_no     .eq(result_no + 1),
            ]

            with m.If(result_no == (self.stages - 1)):
                m.d.sync += iteration.eq(iteration + 1)

            with m.If(escape | maxed_out):
                m.d.sync += [
                    self.escape_out[result_no].eq(escape),
                    self.iterations_out[result_no].eq(iteration + 1),
                    done[result_no].eq(1),
                ]

        with m.FSM() as fsm:
            m.d.comb += running.eq(~fsm.ongoing("IDLE"))
            with m.State("IDLE"):
                m.d.comb += stage_enable.eq(0b0000)
                with m.If(self.start_in):
                    m.d.sync += [
                        x_stage_0   .eq(0),
                        y_stage_0   .eq(0),
                        xx_stage_0  .eq(0),
                        x_stage_1   .eq(0),
                        y_stage_1   .eq(0),
                        xx_stage_1  .eq(0),
                        yy_stage_1  .eq(0),
                        two_xy      .eq(0),
                        xx_plus_yy  .eq(0),
                        x_stage_3   .eq(0),
                        y_stage_3   .eq(0),

                        iteration             .eq(0),
                        self.result_ready_out .eq(0),
                        result_read           .eq(0),
                        Cat(done)             .eq(0),
                    ]
                    m.next = "PRIME0"

            # prime the pipeline
            with m.State("PRIME0"):
                m.d.comb += stage_enable.eq(0b0001)
                m.next = "PRIME1"

            with m.State("PRIME1"):
                m.d.comb += stage_enable.eq(0b1001)
                m.next = "PRIME2"

            with m.State("PRIME2"):
                m.d.comb += stage_enable.eq(0b1101)
                m.next = "RUNNING"

            with m.State("RUNNING"):
                m.d.comb += stage_enable.eq(0b1111)
                with m.If(Cat(done) == 0b1111):
                    m.d.sync += self.result_ready_out.eq(1)
                    m.next = "IDLE"

        return m

class MandelbrotTest(GatewareTestCase):
    FRAGMENT_UNDER_TEST = Mandelbrot
    FRAGMENT_ARGUMENTS = {'bitwidth': 64, 'fraction_bits': 56, 'test': True}

    def iterate_mandel(self, scale, dut, start_x, start_y, check=True):
        print("=================> mandel start")
        x = start_x
        y = start_y
        done = 0
        yield from self.advance_cycles(4)
        while done == 0:
            x_new = ((x * x) >> scale) - ((y * y) >> scale) + start_x
            y_new = ((x * y) >> (scale - 1)) + start_y
            x = x_new
            y = y_new
            dut_x = (yield dut.x)
            dut_y = (yield dut.y)
            print(f"dut_x: {hex(dut_x)} python x: {hex(x)} | dut_y: {hex(dut_y)} python y: {hex(y)}")
            if check:
                self.assertEqual(dut_x, x)
                self.assertEqual(dut_y, y)
            yield from self.advance_cycles(4)
            done = (yield dut.maxed_out) | (yield dut.escape_out)

        self.assertEqual(done, 1)
        yield dut.result_read_in.eq(1)
        yield
        yield dut.result_read_in.eq(0)
        yield


    @sync_test_case
    def test_basic(self):
        scale = self.FRAGMENT_ARGUMENTS['fraction_bits']
        dut = self.dut
        start_x = 1 << scale
        yield dut.cx_in[0].eq(start_x)
        yield dut.cy_in[0].eq(0)
        yield dut.cx_in[1].eq(start_x << 1)
        yield dut.cy_in[1].eq(0)
        yield dut.cx_in[2].eq(start_x << 2)
        yield dut.cy_in[2].eq(0)
        yield dut.cx_in[3].eq(start_x << 3)
        yield dut.cy_in[3].eq(0)
        yield dut.max_iterations_in.eq(110)
        yield
        yield from self.pulse(dut.start_in)
        yield
        yield

        #self.assertEqual((yield dut.x), start_x)
        yield from self.advance_cycles(16)

        # 1 * 1 + 1 = 2
        first_iter = start_x + start_x
        self.assertEqual((yield dut.x), first_iter)
        yield from self.advance_cycles(4)

        # 2 * 2 + 1 = 5
        second_iter = (first_iter * first_iter >> scale) + start_x
        self.assertEqual((yield dut.x), second_iter)
        yield
        yield
        self.assertGreater((yield dut.xx_plus_yy), 4 << scale)
        yield from self.advance_cycles(4)

        self.assertEqual((yield dut.escape_out), 1)

        yield dut.result_read_in.eq(1)
        yield
        yield dut.result_read_in.eq(0)
        yield

        start_x = 1 << (scale - 1)
        start_y = 0
        yield dut.cx_in.eq(start_x)
        yield dut.cy_in.eq(start_y)
        yield
        yield from self.pulse(dut.start_in)
        yield
        yield from self.iterate_mandel(scale, dut, start_x, start_y)

        yield
        yield

        start_x = 0
        start_y = 1 << (scale - 1)
        yield dut.cx_in.eq(start_x)
        yield dut.cy_in.eq(start_y)
        yield
        yield from self.pulse(dut.start_in)
        yield
        yield from self.iterate_mandel(scale, dut, start_x, start_y)

        yield
        yield

        start_x = 1 << (scale - 2)
        start_y = 1 << (scale - 3)
        yield dut.cx_in.eq(start_x)
        yield dut.cy_in.eq(start_y)
        yield
        yield from self.pulse(dut.start_in)
        yield
        yield from self.iterate_mandel(scale, dut, start_x, start_y)
        yield
        self.assertEqual((yield dut.result_ready_out), 0)
        yield
