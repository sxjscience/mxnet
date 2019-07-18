/*
 * Licensed to the Apache Software Foundation (ASF) under one or more
 * contributor license agreements.  See the NOTICE file distributed with
 * this work for additional information regarding copyright ownership.
 * The ASF licenses this file to You under the Apache License, Version 2.0
 * (the "License"); you may not use this file except in compliance with
 * the License.  You may obtain a copy of the License at
 *
 *    http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package org.apache.mxnet

import org.apache.mxnet.Base._
import org.slf4j.{Logger, LoggerFactory}

import scala.collection.mutable.ArrayBuffer

object Executor {
  // Get the dictionary given name and ndarray pairs.
  private[mxnet] def getDict(names: Seq[String],
                             ndarrays: Seq[NDArray]): Map[String, NDArray] = {
    require(names.toSet.size == names.length, s"Duplicate names detected in ($names)")
    (names zip ndarrays).toMap
  }
}

/**
 * Symbolic Executor component of MXNet <br />
 * <b>
 * WARNING: it is your responsibility to clear this object through dispose().
 * </b>
 *
 * @author Yizhi Liu
 *
 * Constructor: please use Symbol.bind and Symbol.simpleBind instead.
 * @param handle ExecutorHandle generated by calling Bind
 * @param symbol
 * @see Symbol.bind : to create executor
 */
class Executor private[mxnet](private[mxnet] val handle: ExecutorHandle,
                              private[mxnet] val symbol: Symbol,
                              private[mxnet] var argArrays: Array[NDArray] = null,
                              private[mxnet] var gradArrays: Array[NDArray] = null,
                              private[mxnet] var auxArrays: Array[NDArray] = null,
                              private var _ctx: Context = null,
                              private var _gradsReq: Iterable[_] = null,
                              private var _group2ctx: Map[String, Context] = null
                             ) extends NativeResource {

  val outputs: Array[NDArray] = getOutputs
  protected var _argDict: Map[String, NDArray] = null
  protected var _gradDict: Map[String, NDArray] = null
  protected var _auxDict: Map[String, NDArray] = null
  protected var monitorCallback: MXMonitorCallback = null
  private val logger: Logger = LoggerFactory.getLogger(classOf[Executor])

  private var reshaped = false

  override def nativeAddress: CPtrAddress = handle
  override def nativeDeAllocator: (CPtrAddress => Int) = _LIB.mxExecutorFree
  // cannot determine the off-heap size of this object
  override val bytesAllocated: Long = 0
  override val ref: NativeResourceRef = super.register()

  override def dispose(): Unit = {
    if (!super.isDisposed) {
      super.dispose()
      outputs.foreach(o => o.dispose())
      if (reshaped && argArrays != null) {argArrays.foreach(a => a.dispose())}
      if (reshaped && gradArrays != null) {gradArrays.foreach(
        // Symbol will sometimes fill this with nulls so we've got to check the elements too
        a => if (a != null) {a.dispose()})
      }
      if (reshaped && auxArrays != null) {auxArrays.foreach(a => a.dispose())}
    }
  }

  /**
   * Return a new executor with the same symbol and shared memory,
   * but different input/output shapes.
   * For runtime reshaping, variable length sequences, etc.
   * The returned executor shares state with the current one,
   * and cannot be used in parallel with it.
   * @param partialShaping Whether to allow changing the shape of unspecified arguments.
   * @param allowUpSizing Whether to allow allocating new ndarrays that's larger than the original.
   * @param kwargs Map of string to Shape.
   *                - new shape for arguments.
   * @return
   * executor A new executor that shares memory with this.
   */
  def reshape(partialShaping: Boolean = false, allowUpSizing: Boolean = false,
    kwargs: Map[String, Shape]): Executor = {

    val providedArgShapeNames = kwargs.keys
    val providedArgShapeData = kwargs.values.flatMap(_.toVector)
    val providedArgShapeIdx = kwargs.values.scanLeft(0)((sum, shape) => sum + shape.size)

    val ctxMapKeys = if (_group2ctx != null) _group2ctx.keys.toArray else Array.empty[String]
    val ctxMapDevTypes = if (_group2ctx != null) {
      _group2ctx.values.map(_.deviceTypeid).toArray
    } else {
      Array.empty[Int]
    }
    val ctxMapDevIds = if (_group2ctx != null) {
      _group2ctx.values.map(_.deviceId).toArray
    } else {
      Array.empty[Int]
    }

    val inArgs = ArrayBuffer.empty[NDArrayHandle]
    val argGrads = ArrayBuffer.empty[NDArrayHandle]
    val auxStates = ArrayBuffer.empty[NDArrayHandle]
    val outHandle = new ExecutorHandleRef()

    checkCall(_LIB.mxExecutorReshape(
              if (partialShaping) 1 else 0,
              if (allowUpSizing) 1 else 0,
              _ctx.deviceTypeid,
              _ctx.deviceId,
              ctxMapKeys.toArray,
              ctxMapDevTypes.toArray,
              ctxMapDevIds.toArray,
              providedArgShapeNames.toArray,
              providedArgShapeData.toArray,
              providedArgShapeIdx.toArray,
              inArgs,
              argGrads,
              auxStates,
              this.handle,
              outHandle))

    val argArrays = inArgs.map(new NDArray(_)).toArray
    val gradArrays = argGrads.map(handle =>
      if (handle == 0) null else new NDArray(handle)).toArray
    val auxArrays = auxStates.map(new NDArray(_)).toArray

    val executor = new Executor(outHandle.value, this.symbol)
    executor._ctx = this._ctx
    executor._gradsReq = this._gradsReq
    executor._group2ctx = this._group2ctx
    executor.argArrays = argArrays
    executor.gradArrays = gradArrays
    executor.auxArrays = auxArrays
    executor.reshaped = true
    executor
  }

  /**
   * list all the output ndarray
   * @return A list of ndarray binded to the heads of executor.
   */
  private def getOutputs: Array[NDArray] = {
    val ndHandles = ArrayBuffer[NDArrayHandle]()
    checkCall(_LIB.mxExecutorOutputs(handle, ndHandles))
    ndHandles.toArray.map(ele => {
        val nd = new NDArray(ele, addToCollector = false)
        if (nd.isSparse) {
          nd.asInstanceOf[SparseNDArray]
        }
        nd
      }
    )
  }

  /**
   * Calculate the outputs specified by the binded symbol.
   * @param isTrain whether this forward is for evaluation purpose.
   * @param kwargs Additional specification of input arguments.
   */
  def forward(isTrain: Boolean, kwargs: (String, NDArray)*): Unit = {
    kwargs.foreach { case (name, array) =>
      require(argDict.contains(name), s"Unknown argument $name")
      array.copyTo(argDict(name))
    }
    checkCall(_LIB.mxExecutorForward(handle, if (isTrain) 1 else 0))
  }

  def forward(): Unit = {
    forward(isTrain = false)
  }

  /**
   * Do backward pass to get the gradient of arguments.
   * @param outGrads Gradient on the outputs to be propagated back.
   *                 This parameter is only needed when bind is called
   *                 on outputs that are not a loss function.
   */
  def backward(outGrads: Array[NDArray]): Unit = {
    require(outGrads != null, "outGrads must not be null")
    val ndArrayPtrs = outGrads.map(_.handle)
    checkCall(_LIB.mxExecutorBackward(handle, ndArrayPtrs))
  }

  def backward(outGrad: NDArray): Unit = {
    require(outGrad != null, "outGrads must not be null")
    backward(Array(outGrad))
  }

  def backward(): Unit = {
    backward(Array.empty[NDArray])
  }

  /**
   * Install callback.
   * @param callback Takes a string and an NDArrayHandle.
   */
  def setMonitorCallback(callback: MXMonitorCallback): Unit = {
    monitorCallback = callback
    checkCall(_LIB.mxExecutorSetMonitorCallback(handle, monitorCallback))
  }

  /**
   * Get dictionary representation of argument arrrays.
   * @return The dictionary that maps name of arguments to NDArrays.
   */
  def argDict: Map[String, NDArray] = {
    if (_argDict == null) {
      _argDict = Executor.getDict(symbol.listArguments(), argArrays)
    }
    _argDict
  }

  /**
   * Get dictionary representation of gradient arrays.
   * @return The dictionary that maps name of arguments to gradient arrays.
   */
  def gradDict: Map[String, NDArray] = {
    if (_gradDict == null) {
      _gradDict = Executor.getDict(symbol.listArguments(), gradArrays)
    }
    _gradDict
  }

  /**
   * Get dictionary representation of auxiliary states arrays.
   * @return The dictionary that maps name of auxiliary states to NDArrays.
   */
  def auxDict: Map[String, NDArray] = {
    if (_auxDict == null) {
      _auxDict = Executor.getDict(symbol.listAuxiliaryStates(), auxArrays)
    }
    _auxDict
  }

  /**
   * Copy parameters from arg_params, aux_params into executor's internal array.
   * @param argParams : dict of name to NDArray of arguments
   * @param auxParams : dict of name to NDArray of auxiliary states.
   * @param allowExtraParams
   *        Whether allow extra parameters that are not needed by symbol
   *        If this is True, no error will be thrown when arg_params or aux_params
   *        contain extra parameters that is not needed by the executor.
   */
  def copyParamsFrom(argParams: Map[String, NDArray],
                     auxParams: Map[String, NDArray],
                     allowExtraParams: Boolean = false): Unit = {
    argParams.foreach { case (name, array) =>
      if (argDict.contains(name)) {
        array.copyTo(argDict(name))
      } else {
        require(allowExtraParams, s"Provided name $name is not in the arguments")
      }
    }
    if (auxParams != null) {
      auxParams.foreach { case (name, array) =>
        if (auxDict.contains(name)) {
          array.copyTo(auxDict(name))
        } else {
          require(allowExtraParams, s"Provided name $name is not in the auxiliary states")
        }
      }
    }
  }

  def copyParamsFrom(argParams: Map[String, NDArray], allowExtraParams: Boolean): Unit = {
    copyParamsFrom(argParams, null, allowExtraParams)
  }

  def copyParamsFrom(argParams: Map[String, NDArray]): Unit = {
    copyParamsFrom(argParams, allowExtraParams = false)
  }

  /**
   * Get a debug string about internal execution plan.
   * @return Debug string of the executor.
   */
  def debugStr: String = {
    val str = new RefString
    checkCall(_LIB.mxExecutorPrint(handle, str))
    str.value
  }

}
